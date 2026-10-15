from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestStockMilling(ClRiceCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.cl_rice_broken_policy = 'farmer'
        cls.bran.with_company(cls.company).standard_price = 150
        cls.husk.with_company(cls.company).standard_price = 20
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'price_walkin': 60, 'date_from': '2026-01-01'})
        cls.mill = cls.env['fleet.vehicle'].create({
            'model_id': cls.tractor.model_id.id, 'license_plate': 'MILL-1', 'cl_machine_category': 'mill'})

    def _company_qty(self, product):
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id), ('owner_id', '=', False),
            ('location_id.usage', '=', 'internal'), ('quantity', '!=', 0)])
        return sum(quants.mapped('quantity')), quants

    def test_single_warehouse_partitions(self):
        company = self.company
        warehouse = company.cl_rice_warehouse_id
        self.assertTrue(warehouse)
        for location in (company.cl_rice_paddy_location_id, company.cl_rice_rice_location_id,
                         company.cl_rice_byproduct_location_id):
            self.assertEqual(location.warehouse_id, warehouse)
            self.assertEqual(location.usage, 'internal')
        self.assertEqual(company.cl_rice_released_location_id.usage, 'customer')

    def test_intake_creates_owned_lot(self):
        intake = self.env['cl.rice.intake'].with_user(self.user_warehouse).create({
            'farmer_season_id': self.fs.id, 'bags': 20, 'gross_weight': 1010})
        intake.action_confirm()
        self.assertEqual(intake.tare_weight, 10.0)
        self.assertEqual(intake.net_weight, 1000.0)
        self.assertEqual(intake.location_id, self.company.cl_rice_paddy_location_id)
        lot = intake.lot_id
        self.assertEqual(lot.name, f"{self.farmer.cl_farmer_code}-2627-PD")
        self.assertEqual(self._owned_qty(self.farmer, self.paddy), 1000.0)
        quant = self.env['stock.quant'].search([('lot_id', '=', lot.id), ('quantity', '>', 0)])
        self.assertEqual(quant.location_id, self.company.cl_rice_paddy_location_id)
        self.assertEqual(lot.cl_bag_qty, 20)
        self.assertEqual(self.fs.state, 'in_storage')
        self._intake(self.fs, gross=505, bags=10)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.paddy_kg, 1500.0)
        self.assertEqual(self.fs.paddy_bags, 30)

    def test_owned_stock_excluded_from_valuation(self):
        self._intake(self.fs)
        quants = self.env['stock.quant'].search([('owner_id', '=', self.farmer.id), ('quantity', '>', 0)])
        self.assertTrue(quants)
        self.assertTrue(all(value == 0 for value in quants.mapped('value')))
        self.assertEqual(self.paddy.with_company(self.company).total_value, 0)

    def test_intake_constraints(self):
        with self.assertRaises(ValidationError):
            self.env['cl.rice.intake'].create({'farmer_season_id': self.fs.id, 'gross_weight': 5, 'tare_weight': 10})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self.env['cl.rice.intake'].create({'farmer_season_id': self.fs.id, 'gross_weight': 50, 'moisture': 120})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self.env['cl.rice.intake'].create({'farmer_season_id': self.fs.id, 'gross_weight': -50})

    def test_milling_byproducts_go_to_the_mill(self):
        self._intake(self.fs)
        job = self.env['cl.rice.milling.job'].with_user(self.user_miller).create({
            'farmer_season_id': self.fs.id, 'input_lot_id': self.fs._cl_find_lot(self.paddy).id,
            'input_kg': 1000, 'input_bags': 20, 'rice_kg': 650, 'rice_bags': 13,
            'broken_kg': 50, 'bran_kg': 100, 'husk_kg': 180, 'vehicle_id': self.mill.id,
        })
        self.assertEqual(job.loss_kg, 20)
        self.assertAlmostEqual(job.recovery_pct, 65.0)
        self.assertEqual(job.fee_amount, 50_000)
        self.assertEqual(job.byproduct_value, 100 * 150 + 180 * 20)
        job.action_done()
        job = job.with_user(self.env.user)  # the mill operator cannot read accounting
        # paddy consumed; rice and broken rice stay the farmer's, unvalued
        self.assertEqual(self._owned_qty(self.farmer, self.paddy), 0)
        self.assertEqual(self._owned_qty(self.farmer, self.rice), 650)
        self.assertEqual(self._owned_qty(self.farmer, self.broken), 50)
        farmer_quants = self.env['stock.quant'].search([
            ('owner_id', '=', self.farmer.id), ('quantity', '>', 0), ('location_id.usage', '=', 'internal')])
        self.assertTrue(all(value == 0 for value in farmer_quants.mapped('value')))
        self.assertEqual(set(farmer_quants.location_id.ids), {self.company.cl_rice_rice_location_id.id})
        # bran and husk are the mill's own valued stock in the by-products partition
        self.assertFalse(self._owned_qty(self.farmer, self.bran))
        bran_qty, bran_quants = self._company_qty(self.bran)
        self.assertEqual(bran_qty, 100)
        self.assertEqual(bran_quants.location_id, self.company.cl_rice_byproduct_location_id)
        self.assertAlmostEqual(sum(bran_quants.mapped('value')), 15_000)
        husk_qty, husk_quants = self._company_qty(self.husk)
        self.assertEqual(husk_qty, 180)
        self.assertAlmostEqual(sum(husk_quants.mapped('value')), 3_600)
        # the fee is charged, nothing is credited to the farmer for the by-products
        self.assertEqual(job.invoice_id.amount_total, 50_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.milling_total, 50_000)
        self.assertEqual(self.fs.funds_held, 0)
        self.assertEqual(self.fs.balance_owed, 50_000)
        self.assertEqual(self.fs.rice_kg, 650)
        self.assertEqual(self.fs.rice_bags, 13)

    def test_broken_rice_kept_by_the_mill(self):
        self.company.cl_rice_broken_policy = 'kept'
        self._intake(self.fs)
        self._mill(self.fs)
        self.assertFalse(self._owned_qty(self.farmer, self.broken))
        self.assertEqual(self._company_qty(self.broken)[0], 50)

    def test_byproducts_replace_the_fee(self):
        self.company.cl_rice_byproduct_fee_policy = 'replace'
        self._intake(self.fs)
        job = self._mill(self.fs)
        self.assertTrue(job.byproducts_pay_fee)
        self.assertEqual(job.fee_amount, 0)
        self.assertFalse(job.invoice_id)
        self.assertEqual(self._company_qty(self.bran)[0], 100)

    def test_cannot_mill_more_than_held(self):
        self._intake(self.fs)
        job = self.env['cl.rice.milling.job'].create({
            'farmer_season_id': self.fs.id, 'input_lot_id': self.fs._cl_find_lot(self.paddy).id,
            'input_kg': 1200, 'rice_kg': 700})
        with self.assertRaises(UserError):
            job.action_done()
        job.write({'input_kg': 1000, 'rice_kg': 900, 'husk_kg': 200})
        with self.assertRaises(UserError):
            job.action_done()  # outputs heavier than the paddy

    def test_walkin_milling_pays_now(self):
        customer = self.env['res.partner'].create({'name': "Walk-in Customer"})
        intake = self.env['cl.rice.intake'].create({
            'intake_type': 'walkin', 'partner_id': customer.id, 'bags': 5, 'gross_weight': 302.5})
        intake.action_confirm()
        job = self.env['cl.rice.milling.job'].create({
            'mode': 'walkin', 'partner_id': customer.id, 'input_lot_id': intake.lot_id.id,
            'input_kg': 300, 'rice_kg': 195, 'bran_kg': 30, 'husk_kg': 60,
            'payment_journal_id': self.cash_journal.id,
        })
        self.assertEqual(job.price_unit, 60)
        job.action_done()
        invoice = job.invoice_id
        self.assertEqual(invoice.journal_id, self.company.cl_rice_walkin_journal_id)
        self.assertEqual(invoice.amount_total, 18_000)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertEqual(self._owned_qty(customer, self.rice), 195)
        self.assertEqual(self._company_qty(self.bran)[0], 30)

    def test_farmer_stock_is_never_reserved_by_mill_deliveries(self):
        self._intake(self.fs)
        self._mill(self.fs)
        warehouse = self.company.cl_rice_warehouse_id
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.out_type_id.id,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': customers.id,
            'move_ids': [(0, 0, {
                'product_id': self.rice.id, 'product_uom_qty': 100, 'uom_id': self.rice.uom_id.id,
                'location_id': warehouse.lot_stock_id.id, 'location_dest_id': customers.id,
            })],
        })
        picking.action_confirm()
        picking.action_assign()
        self.assertFalse(picking.move_ids.move_line_ids, "farmer rice must stay unreserved")
        mill_lot = self.env['cl.rice.season']._cl_get_mill_lot(self.rice, self.season, self.company)
        self.env['stock.quant']._update_available_quantity(
            self.rice, self.company.cl_rice_rice_location_id, 40, lot_id=mill_lot)
        picking.action_assign()
        self.assertEqual(picking.move_ids.move_line_ids.lot_id, mill_lot)
        self.assertEqual(sum(picking.move_ids.move_line_ids.mapped('quantity')), 40)

    def test_storage_cron_is_idempotent(self):
        self.company.write({
            'cl_rice_storage_enabled': True,
            'cl_rice_storage_basis': 'bag',
            'cl_rice_storage_rate': 1_000,
            'cl_rice_storage_free_days': 30,
        })
        self._intake(self.fs)
        Fee = self.env['cl.rice.storage.fee']
        today = fields.Date.context_today(Fee)
        Fee._cron_charge_storage(today + timedelta(days=10))
        self.assertFalse(Fee.search([('farmer_season_id', '=', self.fs.id)]), "still in the free period")
        later = today + timedelta(days=40)
        Fee._cron_charge_storage(later)
        Fee._cron_charge_storage(later)
        fees = Fee.search([('farmer_season_id', '=', self.fs.id)])
        self.assertEqual(len(fees), 1)
        self.assertEqual(fees.amount, 20_000)
        self.assertEqual(fees.invoice_id.invoice_line_ids.account_id, self.company.cl_rice_storage_income_account_id)
        Fee._cron_charge_storage(later + timedelta(days=31))
        self.assertEqual(Fee.search_count([('farmer_season_id', '=', self.fs.id)]), 2)

    def test_storage_can_be_switched_off(self):
        self.company.write({'cl_rice_storage_enabled': False, 'cl_rice_storage_rate': 1_000})
        self._intake(self.fs)
        Fee = self.env['cl.rice.storage.fee']
        Fee._cron_charge_storage(fields.Date.context_today(Fee) + timedelta(days=90))
        self.assertFalse(Fee.search([('farmer_season_id', '=', self.fs.id)]))

    def test_intake_reversal(self):
        intake = self._intake(self.fs)
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.intake', 'res_id': intake.id, 'action': 'reverse', 'reason': "Weighed twice",
        }).action_confirm()
        self.assertEqual(intake.state, 'cancelled')
        self.assertFalse(self._owned_qty(self.farmer, self.paddy))
