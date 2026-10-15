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
        cls.paddy = cls.env.ref('cl_rice_mill.product_paddy')
        cls.rice = cls.env.ref('cl_rice_mill.product_rice')
        cls.bran = cls.env.ref('cl_rice_mill.product_bran')
        cls.broken = cls.env.ref('cl_rice_mill.product_broken')
        cls.w1 = cls.company.cl_rice_paddy_warehouse_id
        cls.w2 = cls.company.cl_rice_rice_warehouse_id
        cls.company.write({
            'cl_rice_bran_policy': 'kept',
            'cl_rice_bran_value': 100,
            'cl_rice_husk_policy': 'kept',
            'cl_rice_husk_value': 0,
            'cl_rice_broken_policy': 'farmer',
        })
        cls.rate_milling = cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling',
            'uom': 'kg',
            'price_farmer': 50,
            'price_walkin': 60,
            'date_from': '2026-01-01',
        })
        cls.mill = cls.env['fleet.vehicle'].create({
            'model_id': cls.tractor.model_id.id,
            'license_plate': 'MILL-1',
            'cl_machine_category': 'mill',
        })

    def _intake(self, fs=None, gross=1010, bags=20, **vals):
        values = {
            'farmer_season_id': (fs or self.fs).id,
            'bags': bags,
            'gross_weight': gross,
            'moisture': 13.5,
            'grade': 'a',
            **vals,
        }
        intake = self.env['cl.rice.intake'].with_user(self.user_warehouse).create(values)
        intake.action_confirm()
        return intake

    def _owned_quants(self, partner, product):
        return self.env['stock.quant'].search([
            ('owner_id', '=', partner.id),
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '!=', 0),
        ])

    def test_intake_creates_owned_lot(self):
        intake = self._intake()
        self.assertEqual(intake.state, 'done')
        self.assertEqual(intake.tare_weight, 10.0)
        self.assertEqual(intake.net_weight, 1000.0)
        lot = intake.lot_id
        self.assertEqual(lot.name, f"{self.farmer.cl_farmer_code}-2627-MP")
        self.assertEqual(lot.cl_farmer_season_id, self.fs)
        quants = self._owned_quants(self.farmer, self.paddy)
        self.assertEqual(sum(quants.mapped('quantity')), 1000.0)
        self.assertEqual(quants.lot_id, lot)
        self.assertEqual(lot.cl_bag_qty, 20)
        self.assertEqual(self.fs.state, 'in_storage')
        # a second intake goes to the same lot
        self._intake(gross=505, bags=10)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.paddy_kg, 1500.0)
        self.assertEqual(self.fs.paddy_bags, 30)
        self.assertEqual(self.fs.collateral_value, 1_500_000)

    def test_owned_stock_excluded_from_valuation(self):
        self._intake()
        quants = self._owned_quants(self.farmer, self.paddy)
        self.assertTrue(quants)
        self.assertTrue(all(value == 0 for value in quants.mapped('value')))
        self.assertEqual(self.paddy.with_company(self.company).total_value, 0)
        moves = quants.lot_id and self.env['stock.move'].search([('cl_lot_id', '=', quants.lot_id.id)])
        self.assertFalse(moves.account_move_id, "no valuation entry for farmer-owned stock")

    def test_intake_constraints(self):
        with self.assertRaises(ValidationError):
            self.env['cl.rice.intake'].create({'farmer_season_id': self.fs.id, 'gross_weight': 5, 'tare_weight': 10})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self.env['cl.rice.intake'].create({'farmer_season_id': self.fs.id, 'gross_weight': 50, 'moisture': 120})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self.env['cl.rice.intake'].create({'farmer_season_id': self.fs.id, 'gross_weight': -50})

    def _milling(self, **vals):
        values = {
            'farmer_season_id': self.fs.id,
            'input_lot_id': self.fs._cl_find_lot(self.paddy).id,
            'input_kg': 1000,
            'input_bags': 20,
            'rice_kg': 650,
            'rice_bags': 13,
            'broken_kg': 50,
            'bran_kg': 100,
            'husk_kg': 180,
            'vehicle_id': self.mill.id,
            **vals,
        }
        return self.env['cl.rice.milling.job'].with_user(self.user_miller).create(values)

    def test_farmer_milling_flows(self):
        self._intake()
        job = self._milling()
        self.assertEqual(job.loss_kg, 20)
        self.assertAlmostEqual(job.recovery_pct, 65.0)
        self.assertAlmostEqual(job.total_recovery_pct, 70.0)
        self.assertEqual(job.fee_amount, 50_000)
        self.assertEqual(job.byproduct_credit, 10_000)
        job.action_done()
        job = job.with_user(self.env.user)  # the mill operator cannot read accounting
        self.assertEqual(job.state, 'done')
        # paddy consumed, outputs in W2 owned by the farmer, kept by-products owned by the mill
        self.assertFalse(self._owned_quants(self.farmer, self.paddy))
        rice_quants = self._owned_quants(self.farmer, self.rice)
        self.assertEqual(sum(rice_quants.mapped('quantity')), 650)
        self.assertTrue(all(q.location_id.warehouse_id == self.w2 for q in rice_quants))
        self.assertEqual(sum(self._owned_quants(self.farmer, self.broken).mapped('quantity')), 50)
        self.assertFalse(self._owned_quants(self.farmer, self.bran))
        mill_bran = self.env['stock.quant'].search([
            ('product_id', '=', self.bran.id), ('owner_id', '=', False), ('location_id.usage', '=', 'internal')])
        self.assertEqual(sum(mill_bran.mapped('quantity')), 100)
        self.assertTrue(all(value == 0 for value in rice_quants.mapped('value')))
        # fee charged to the farmer season, by-products credited to the farmer
        self.assertEqual(job.invoice_id.amount_total, 50_000)
        self.assertEqual(job.invoice_id.cl_farmer_season_id, self.fs)
        credit = job.credit_move_id.line_ids.filtered(lambda l: l.account_id == self.company.cl_rice_proceeds_account_id)
        self.assertEqual(credit.credit, 10_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.milling_total, 50_000)
        self.assertEqual(self.fs.credits_due, 10_000)
        self.assertEqual(self.fs.rice_kg, 650)
        self.assertEqual(self.fs.rice_bags, 13)
        self.assertEqual(self.fs.paddy_kg, 0)

    def test_cannot_mill_more_than_held(self):
        self._intake()
        job = self._milling(input_kg=1200, rice_kg=700)
        with self.assertRaises(UserError):
            job.action_done()
        job = self._milling(input_kg=1000, rice_kg=900, husk_kg=200)
        with self.assertRaises(UserError):
            job.action_done()  # outputs heavier than the paddy

    def test_walkin_milling_pays_now(self):
        customer = self.env['res.partner'].create({'name': "Mteja wa Papo Hapo"})
        intake = self.env['cl.rice.intake'].create({
            'intake_type': 'walkin',
            'partner_id': customer.id,
            'bags': 5,
            'gross_weight': 302.5,
        })
        intake.action_confirm()
        self.assertEqual(intake.lot_id.cl_owner_id, customer)
        job = self.env['cl.rice.milling.job'].create({
            'mode': 'walkin',
            'partner_id': customer.id,
            'input_lot_id': intake.lot_id.id,
            'input_kg': 300,
            'rice_kg': 195,
            'bran_kg': 30,
            'husk_kg': 60,
            'payment_journal_id': self.cash_journal.id,
        })
        self.assertEqual(job.price_unit, 60)
        job.action_done()
        invoice = job.invoice_id
        self.assertEqual(invoice.journal_id, self.company.cl_rice_walkin_journal_id)
        # 300 kg x 60 minus 30 kg of bran kept at 100
        self.assertEqual(invoice.amount_total, 15_000)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertFalse(invoice.cl_farmer_season_id)
        self.assertEqual(sum(self._owned_quants(customer, self.rice).mapped('quantity')), 195)

    def test_farmer_stock_is_never_reserved_by_mill_deliveries(self):
        self._intake()
        self._milling().action_done()
        # a normal delivery of rice from W2 must not take the farmer's rice
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.w2.out_type_id.id,
            'location_id': self.w2.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'move_ids': [(0, 0, {
                'product_id': self.rice.id,
                'product_uom_qty': 100,
                'uom_id': self.rice.uom_id.id,
                'location_id': self.w2.lot_stock_id.id,
                'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            })],
        })
        picking.action_confirm()
        picking.action_assign()
        self.assertFalse(picking.move_ids.move_line_ids, "farmer rice must stay unreserved")
        # mill-owned rice is reserved normally
        mill_lot = self.env['cl.rice.season']._cl_get_mill_lot(self.rice, self.season, self.company)
        self.env['stock.quant']._update_available_quantity(self.rice, self.w2.lot_stock_id, 40, lot_id=mill_lot)
        picking.action_assign()
        self.assertEqual(picking.move_ids.move_line_ids.lot_id, mill_lot)
        self.assertEqual(sum(picking.move_ids.move_line_ids.mapped('quantity')), 40)
        self.assertFalse(picking.move_ids.move_line_ids.owner_id)

    def test_storage_cron_is_idempotent(self):
        self.company.write({
            'cl_rice_storage_basis': 'bag',
            'cl_rice_storage_rate': 1_000,
            'cl_rice_storage_free_days': 30,
        })
        self._intake()
        Fee = self.env['cl.rice.storage.fee']
        today = fields.Date.context_today(Fee)
        Fee._cron_charge_storage(today + timedelta(days=10))
        self.assertFalse(Fee.search([('farmer_season_id', '=', self.fs.id)]), "still in the free period")
        later = today + timedelta(days=40)
        Fee._cron_charge_storage(later)
        Fee._cron_charge_storage(later)
        fees = Fee.search([('farmer_season_id', '=', self.fs.id)])
        self.assertEqual(len(fees), 1)
        self.assertEqual(fees.quantity, 20)
        self.assertEqual(fees.amount, 20_000)
        self.assertEqual(fees.invoice_id.amount_total, 20_000)
        self.assertEqual(
            fees.invoice_id.invoice_line_ids.account_id, self.company.cl_rice_storage_income_account_id)
        # the next month is charged once more
        Fee._cron_charge_storage(later + timedelta(days=31))
        self.assertEqual(Fee.search_count([('farmer_season_id', '=', self.fs.id)]), 2)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.storage_total, 40_000)

    def test_intake_reversal(self):
        intake = self._intake()
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.intake',
            'res_id': intake.id,
            'action': 'reverse',
            'reason': "Weighed twice",
        }).action_confirm()
        self.assertEqual(intake.state, 'cancelled')
        self.assertFalse(self._owned_quants(self.farmer, self.paddy))
