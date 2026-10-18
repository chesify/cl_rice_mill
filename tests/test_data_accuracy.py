import base64
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestDataAccuracy(ClRiceCommon):
    """Phase 3 of the fix round: dates, bags, walk-in collection, setup, import, stages and labels."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'price_walkin': 60, 'date_from': '2026-01-01'})

    # ------------------------------------------------------------------
    # G12: stock dated at the intake date
    # ------------------------------------------------------------------
    def test_intake_moves_follow_the_intake_date(self):
        intake = self._intake(self.fs, date=datetime(2026, 8, 3, 9, 30))
        moves = intake.picking_id.move_ids
        self.assertEqual(moves.mapped('date'), [datetime(2026, 8, 3, 9, 30)])
        self.assertEqual(moves.move_line_ids.mapped('date'), [datetime(2026, 8, 3, 9, 30)])
        self.assertEqual(intake.lot_id.cl_first_in_date, datetime(2026, 8, 3, 9, 30))

    def test_storage_free_period_counts_from_the_intake_date(self):
        self.company.write({'cl_rice_storage_enabled': True, 'cl_rice_storage_basis': 'bag',
                            'cl_rice_storage_rate': 1_000, 'cl_rice_storage_free_days': 7})
        today = fields.Date.context_today(self.env.user)
        self._intake(self.fs, date=datetime.combine(today - timedelta(days=20), datetime.min.time()))
        self.env['cl.rice.storage.fee']._cron_charge_storage(today)
        fees = self.env['cl.rice.storage.fee'].search([('farmer_season_id', '=', self.fs.id)])
        self.assertEqual(len(fees), 1, "received 20 days ago: past the 7 free days")

    def test_opening_stock_dated_at_the_batch_date(self):
        headers = "Farmer Code,Farmer Name,Paddy Bags,Paddy Kg"
        content = "\n".join([headers, f"{self.farmer.cl_farmer_code},{self.farmer.name},10,900"]).encode()
        batch = self.env['cl.rice.opening.balance'].with_user(self.user_manager).create({
            'season_id': self.season.id, 'date': '2026-07-01',
            'file': base64.b64encode(content).decode(), 'file_name': 'b.csv'})
        batch.action_import_file()
        batch.action_post()
        moves = batch.line_ids.picking_ids.move_ids
        self.assertEqual(moves.mapped('date'), [datetime(2026, 7, 1)])

    # ------------------------------------------------------------------
    # G13: bags follow the kg
    # ------------------------------------------------------------------
    def test_milling_without_bags_takes_the_lot_average(self):
        self._intake(self.fs, gross=2010, bags=20)  # 2000 kg net in 20 bags: 100 kg a bag
        job = self.env['cl.rice.milling.job'].create({
            'farmer_season_id': self.fs.id, 'input_lot_id': self.fs._cl_find_lot(self.paddy).id,
            'input_kg': 1000, 'rice_kg': 650, 'broken_kg': 50, 'bran_kg': 100, 'husk_kg': 180,
        })
        self.assertEqual(job.input_bags, 10)
        self.assertEqual(job.rice_bags, 13)  # 650 kg / 50 kg bags
        self.assertEqual(job.broken_bags, 1)
        job.action_done()
        lot = self.fs._cl_find_lot(self.paddy)
        lot.invalidate_recordset()
        self.assertEqual(lot.cl_bag_qty, 10, "the bags milled leave the lot")
        self.assertEqual(lot._cl_bags_for_fee(), 10)

    def test_entered_bags_are_kept(self):
        self._intake(self.fs, gross=2010, bags=20)
        job = self.env['cl.rice.milling.job'].create({
            'farmer_season_id': self.fs.id, 'input_lot_id': self.fs._cl_find_lot(self.paddy).id,
            'input_kg': 1000, 'input_bags': 12, 'rice_kg': 650, 'rice_bags': 14, 'husk_kg': 300,
        })
        self.assertEqual((job.input_bags, job.rice_bags), (12, 14))

    # ------------------------------------------------------------------
    # G14: walk-in collection
    # ------------------------------------------------------------------
    def _walkin_rice(self):
        customer = self.env['res.partner'].create({'name': "Walk-in Customer"})
        intake = self.env['cl.rice.intake'].create({
            'intake_type': 'walkin', 'partner_id': customer.id, 'bags': 5, 'gross_weight': 302.5})
        intake.action_confirm()
        job = self.env['cl.rice.milling.job'].create({
            'mode': 'walkin', 'partner_id': customer.id, 'input_lot_id': intake.lot_id.id,
            'input_kg': 300, 'rice_kg': 200, 'bran_kg': 30, 'husk_kg': 60,
            'payment_journal_id': self.cash_journal.id,
        })
        job.action_done()
        lot = job.output_picking_id.move_line_ids.filtered(lambda l: l.product_id == self.rice).lot_id
        return customer, lot

    def test_walkin_collection(self):
        customer, lot = self._walkin_rice()
        collection = self.env['cl.rice.walkin.collection'].create({
            'partner_id': customer.id, 'lot_id': lot.id, 'quantity': 200, 'vehicle_plate': 'T 555 XYZ'})
        self.assertEqual(collection.bags, 4)  # 200 kg in 50 kg bags
        collection.with_user(self.user_warehouse).action_release()
        self.assertEqual(collection.state, 'released')
        self.assertEqual(self._owned_qty(customer, self.rice), 0)
        self.assertEqual(collection.picking_id.location_dest_id, customer.property_stock_customer)
        report = self.env['ir.actions.report']._render_qweb_html(
            'cl_rice_mill.action_report_walkin_gate_pass', collection.ids)[0]
        self.assertIn(b'T 555 XYZ', report)

    def test_walkin_collection_blocked_by_unpaid_storage(self):
        self.company.write({'cl_rice_storage_enabled': True, 'cl_rice_storage_basis': 'kg',
                            'cl_rice_storage_rate': 10, 'cl_rice_storage_free_days': 7})
        customer, lot = self._walkin_rice()
        today = fields.Date.context_today(self.env.user)
        fees = self.env['cl.rice.storage.fee']._cl_charge_company(self.company, today + timedelta(days=40))
        self.assertTrue(fees.filtered(lambda f: f.partner_id == customer).invoice_id)
        collection = self.env['cl.rice.walkin.collection'].create({
            'partner_id': customer.id, 'lot_id': lot.id, 'quantity': 100})
        self.assertTrue(collection.unpaid_amount)
        with self.assertRaisesRegex(UserError, "must first pay"):
            collection.with_user(self.user_warehouse).action_release()
        with self.assertRaises(UserError):
            self.env['cl.rice.walkin.collection'].create({
                'partner_id': customer.id, 'lot_id': lot.id, 'quantity': 5_000}).with_user(
                self.user_warehouse).action_release()

    def test_farmer_lot_cannot_be_collected_as_walkin(self):
        self._intake(self.fs)
        collection = self.env['cl.rice.walkin.collection'].create({
            'partner_id': self.farmer.id, 'lot_id': self.fs._cl_find_lot(self.paddy).id, 'quantity': 100})
        with self.assertRaisesRegex(UserError, "stock release"):
            collection.with_user(self.user_warehouse).action_release()

    # ------------------------------------------------------------------
    # G15: by-products and the rice warehouse
    # ------------------------------------------------------------------
    def test_setup_byproduct_prices_and_warehouse(self):
        wizard = self.env['cl.rice.setup'].create({
            'create_payment_journals': False, 'bran_price': 300, 'bran_cost': 120, 'husk_price': 50, 'husk_cost': 20,
            'archive_default_warehouse': True})
        wizard.action_apply()
        bran = self.bran.with_company(self.company)
        self.assertEqual((bran.list_price, bran.standard_price), (300, 120))
        self.assertEqual(self.husk.with_company(self.company).standard_price, 20)
        warehouses = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)])
        self.assertIn(self.company.cl_rice_warehouse_id, warehouses)

    def test_rice_products_sold_from_the_rice_warehouse(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [(0, 0, {'product_id': self.bran.id, 'product_uom_qty': 100})],
        })
        self.assertEqual(order.warehouse_id, self.company.cl_rice_warehouse_id)
        self.assertEqual(self.user_warehouse.with_company(self.company)._get_default_warehouse_id(),
                         self.company.cl_rice_warehouse_id)

    # ------------------------------------------------------------------
    # G16: opening balance columns
    # ------------------------------------------------------------------
    def test_opening_balance_acreage_officer_and_activity(self):
        headers = "Farmer Code,Farmer Name,Phone,NIDA,Acreage,Field Officer,Loan"
        loan = self._create_loan(self.fs, 50_000)
        self._disburse(loan)
        content = "\n".join([
            headers,
            f",Neema Mbwile,255755123123,19900101-12345-00001-11,4,{self.user_officer.login},100000",
            f"{self.farmer.cl_farmer_code},{self.farmer.name},,,,,20000",
            ",Bad Officer,255755999999,,1,nobody,5000",
        ]).encode()
        batch = self.env['cl.rice.opening.balance'].with_user(self.user_manager).create({
            'season_id': self.season.id, 'date': '2026-07-01',
            'file': base64.b64encode(content).decode(), 'file_name': 'b.csv'})
        batch.action_import_file()
        new, existing, bad = batch.line_ids
        self.assertFalse(new.error)
        self.assertEqual(new.field_officer_id, self.user_officer)
        self.assertIn("activity", existing.warning)
        self.assertFalse(existing.error, "a warning does not block")
        self.assertIn("not found", bad.error)
        bad.unlink()
        batch.action_validate()
        batch.action_post()
        fs = new.farmer_season_id
        self.assertEqual(fs.acreage, 4)
        self.assertEqual(fs.field_officer_id, self.user_officer)
        self.assertEqual(new.partner_id.cl_field_officer_id, self.user_officer)
        self.assertTrue(fs.credit_limit > 0, "the credit limit follows the acreage")
        self.assertFalse(fs.over_limit if 'over_limit' in fs else False)

    def test_template_has_the_new_columns(self):
        template = self.env['cl.rice.opening.balance']._cl_template_xlsx()
        rows = self.env['cl.rice.opening.balance']._cl_read_rows(template, 'template.xlsx')
        self.assertEqual(rows[0]['acreage'], 3)
        self.assertEqual(rows[0]['field_officer'], 'officer.login')

    # ------------------------------------------------------------------
    # G19: task stages follow the farmer season
    # ------------------------------------------------------------------
    def test_task_stages(self):
        stages = self.env['project.task.type'].search([('project_ids', 'in', self.season.project_id.ids)])
        self.assertEqual(stages.sorted('sequence').mapped('name'), [
            "Registered", "Land Prep", "Planting", "Harvest", "In Storage", "Milling", "Released", "Settled"])
        task = self.fs.task_id.sudo()
        self.assertEqual(task.stage_id.cl_rice_stage, 'registered')
        job = self.env['cl.rice.service.job'].create({
            'farmer_season_id': self.fs.id, 'service_type': 'ploughing', 'date': '2026-08-01', 'quantity': 2})
        job.action_done()
        job.action_charge()
        self.assertEqual(task.stage_id.cl_rice_stage, 'land_prep')
        task.sudo().stage_id = self.season._cl_task_stage('planting')  # moved by hand
        self.fs.action_mark_harvested()
        self.assertEqual(task.stage_id.cl_rice_stage, 'harvest')
        self._intake(self.fs)
        self.assertEqual(task.stage_id.cl_rice_stage, 'in_storage')
        self._mill(self.fs)
        self.assertEqual(task.stage_id.cl_rice_stage, 'milling')
        self.fs.with_context(cl_rice_unlock=True).state = 'in_storage'
        self.assertEqual(task.stage_id.cl_rice_stage, 'milling', "tasks never move back")

    # ------------------------------------------------------------------
    # G20 - G24
    # ------------------------------------------------------------------
    def test_storage_is_not_a_service_type(self):
        types = dict(self.env['cl.rice.service.rate']._fields['service_type'].selection)
        self.assertNotIn('storage', types)
        self.assertNotIn('storage', dict(self.env['cl.rice.service.job']._fields['service_type'].selection))

    def test_loan_journal_follows_the_method(self):
        Journal = self.env['account.journal']
        mpesa = Journal.search([('code', '=', 'MPS'), ('company_id', '=', self.company.id)]) or Journal.create(
            {'name': "M-Pesa", 'code': 'MPS', 'type': 'bank', 'company_id': self.company.id})
        loan = self.env['cl.rice.loan'].create({
            'farmer_season_id': self.fs.id, 'amount': 10_000, 'disbursement_method': 'mobile_money'})
        self.assertEqual(loan.journal_id, mpesa)
        loan.disbursement_method = 'cash'
        self.assertEqual(loan.journal_id.type, 'cash')

    def test_friendly_settlement_labels(self):
        loan = self._disburse(self._create_loan(self.fs, 100_000, purpose="Seeds"))
        action = self.fs.with_user(self.user_accountant).action_open_settle()
        wizard = self.env['cl.rice.settlement.create'].with_user(self.user_accountant).with_context(
            action['context']).create({'payout_journal_id': self.cash_journal.id})
        settlement = self.env['cl.rice.settlement'].browse(wizard.action_create()['res_id'])
        names = settlement.line_ids.filtered(lambda l: l.line_type == 'open').mapped('name')
        self.assertIn(f"Loan {loan.name} - land preparation", names)
        self.assertFalse([name for name in names if 'Manual Payment' in name or 'Payment' in name])

    def test_milling_start_keeps_the_date(self):
        self._intake(self.fs)
        job = self.env['cl.rice.milling.job'].create({
            'farmer_season_id': self.fs.id, 'input_lot_id': self.fs._cl_find_lot(self.paddy).id,
            'date_start': '2026-08-10 07:00:00', 'date_end': '2026-08-10 12:00:00',
            'input_kg': 1000, 'rice_kg': 650, 'husk_kg': 300})
        job.action_start()
        self.assertEqual(job.date_start, datetime(2026, 8, 10, 7))
        job.action_done()
        self.assertEqual(job.state, 'done')

    def test_analytic_account_names_the_farmer_only(self):
        account = self.fs.analytic_account_id
        self.assertEqual(account.name, f"{self.farmer.cl_farmer_code} {self.farmer.name}")
        self.assertEqual(account.plan_id, self.season.analytic_plan_id)
