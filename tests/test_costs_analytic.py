import importlib.util
import uuid

from odoo.tests import tagged
from odoo.tools import file_path

from .common import ClRiceCommon


def load_migration(version):
    path = file_path(f'cl_rice_mill/migrations/{version}/post-migrate.py')
    spec = importlib.util.spec_from_file_location(f'cl_rice_mill_migration_{version}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestCostsAnalytic(ClRiceCommon):
    """Phase 2 of the fix round: season project (G9), expenses (G10), machine costs (G6), setup (G17), trips (G18)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.cl_rice_fuel_price = 3_000
        cls.rate_ploughing.operator_rate = 5_000
        cls.project_account = cls.season.project_id.account_id
        cls.motorbike = cls.env['fleet.vehicle'].create({
            'model_id': cls.tractor.model_id.id, 'license_plate': 'MC 111 AAA', 'cl_machine_category': 'motorcycle'})
        cls.officer_employee = cls.env['hr.employee'].sudo().create({
            'name': "Officer Employee", 'user_id': cls.user_officer.id})

    def _accounts_of(self, distribution):
        return {int(i) for key in distribution or {} for i in key.split(',')}

    def _job(self, **vals):
        job = self.env['cl.rice.service.job'].create({
            'farmer_season_id': self.fs.id, 'service_type': 'ploughing', 'date': '2026-08-01',
            'vehicle_id': self.tractor.id, 'operator_id': self.operator.id, 'quantity': 4, 'fuel_litres': 20, **vals})
        job.action_done()
        job.action_charge()
        return job

    # ------------------------------------------------------------------
    # G9: the season project
    # ------------------------------------------------------------------
    def test_season_project_account(self):
        account = self.project_account
        self.assertTrue(account)
        self.assertEqual(account.plan_id, self.env.ref('analytic.analytic_plan_projects'))
        self.assertEqual(account.code, 'PRJ-2627')
        self.assertEqual(account.name, self.season.name)

    def test_every_distribution_carries_the_project(self):
        job = self._job()
        income = job.invoice_id.invoice_line_ids
        self.assertIn(self.project_account.id, self._accounts_of(income.analytic_distribution))
        cost_lines = job.cost_move_id.line_ids.filtered('analytic_distribution')
        self.assertTrue(cost_lines)
        for line in cost_lines:
            self.assertIn(self.project_account.id, self._accounts_of(line.analytic_distribution))

    def test_pickings_carry_the_project(self):
        self.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        intake = self._intake(self.fs)
        job = self._mill(self.fs)
        for picking in intake.picking_id | job.consume_picking_id | job.output_picking_id:
            self.assertEqual(picking.project_id, self.season.project_id, picking.name)

    def test_vendor_bill_defaults_to_the_project(self):
        diesel = self.env.ref('cl_rice_mill.product_expense_diesel')
        repairs = self.env.ref('cl_rice_mill.product_expense_repairs')
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id, 'invoice_date': '2026-08-01',
            'invoice_line_ids': [
                (0, 0, {'product_id': diesel.id, 'quantity': 100, 'price_unit': 3_000, 'tax_ids': [(6, 0, [])]}),
                (0, 0, {'product_id': repairs.id, 'quantity': 1, 'price_unit': 80_000, 'tax_ids': [(6, 0, [])]}),
            ],
        })
        fuel_line, repair_line = bill.invoice_line_ids
        # diesel goes to fuel clearing (a balance sheet account): no machine, no project, no double count
        self.assertEqual(fuel_line.account_id, self.company.cl_rice_fuel_clearing_account_id)
        self.assertFalse(fuel_line.analytic_distribution)
        self.assertEqual(repair_line.account_id, self.company.cl_rice_repair_account_id)
        self.assertIn(self.project_account.id, self._accounts_of(repair_line.analytic_distribution))

    def test_migration_reuses_hand_made_account(self):
        # the test database state: a hand-made project account and journal items tagged by hand or not at all
        project = self.season.project_id
        own_account = project.account_id
        manual = self.env['account.analytic.account'].create({
            'name': "Msimu 2026/27 [MCP-BACKFILL]", 'code': 'MCP-BF-2627',
            'plan_id': self.env.ref('analytic.analytic_plan_projects').id, 'company_id': self.company.id})
        project.sudo().account_id = False
        job = self._job()
        untagged = job.invoice_id.invoice_line_ids
        fs_key = str(self.fs.analytic_account_id.id)
        untagged.analytic_distribution = {fs_key: 100}
        hand_tagged = job.cost_move_id.line_ids.filtered('analytic_distribution')[:1]
        hand_tagged.analytic_distribution = {fs_key: 100, str(manual.id): 100}
        own_account.sudo().unlink()
        migration = load_migration('20.0.3.0.0')
        migration.migrate(self.env.cr, '20.0.2.0.0')
        self.assertEqual(project.account_id, manual, "the hand-made account is reused")
        self.assertEqual(manual.code, 'PRJ-2627')
        self.assertEqual(manual.name, self.season.name)
        self.assertEqual(untagged.analytic_distribution, {f"{fs_key},{manual.id}": 100})
        self.assertEqual(hand_tagged.analytic_distribution, {fs_key: 100, str(manual.id): 100}, "not tagged twice")
        accounts = self.env['account.analytic.account'].search([('plan_id', '=', manual.plan_id.id), ('code', 'like', 'PRJ-2627')])
        self.assertEqual(accounts, manual)

    # ------------------------------------------------------------------
    # G6: machine costs
    # ------------------------------------------------------------------
    def test_job_cost_posted_to_the_machine(self):
        job = self._job()
        self.assertEqual(job.fuel_cost, 60_000)  # 20 L x 3,000
        self.assertEqual(job.operator_cost, 20_000)  # 4 acres x 5,000
        lines = job.cost_move_id.line_ids
        fuel = lines.filtered(lambda l: l.account_id == self.company.cl_rice_machine_cost_account_id)
        wages = lines.filtered(lambda l: l.account_id == self.company.cl_rice_operator_wage_account_id)
        self.assertEqual(fuel.debit, 60_000)
        self.assertEqual(wages.debit, 20_000)
        self.assertEqual(lines.filtered(lambda l: l.account_id == self.company.cl_rice_fuel_clearing_account_id).credit, 60_000)
        accrual = lines.filtered(lambda l: l.account_id == self.company.cl_rice_operator_accrual_account_id)
        self.assertEqual(accrual.partner_id, self.operator.work_contact_id)
        # machine profit = income - fuel - wages, all through its analytic account
        machine = self.tractor.cl_analytic_account_id
        plan_column = machine.plan_id._column_name()
        machine_lines = self.env['account.analytic.line'].search([(plan_column, '=', machine.id)])
        self.assertEqual(sum(machine_lines.mapped('amount')), 200_000 - 60_000 - 20_000)

    def test_free_job_still_costs_the_machine(self):
        job = self._job(is_free=True)
        self.assertFalse(job.invoice_id)
        self.assertEqual(sum(job.cost_move_id.line_ids.mapped('debit')), 80_000)

    def test_no_double_count_with_fuel_bills(self):
        diesel = self.env.ref('cl_rice_mill.product_expense_diesel')
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id, 'invoice_date': '2026-08-01',
            'invoice_line_ids': [(0, 0, {'product_id': diesel.id, 'quantity': 100, 'price_unit': 3_000,
                                         'tax_ids': [(6, 0, [])]})],
        })
        bill.action_post()
        self._job()
        clearing = self.company.cl_rice_fuel_clearing_account_id
        balance = sum(self.env['account.move.line'].search(
            [('account_id', '=', clearing.id), ('parent_state', '=', 'posted')]).mapped('balance'))
        self.assertEqual(balance, 300_000 - 60_000, "the tank holds what was bought minus what was used")
        expense = sum(self.env['account.move.line'].search([
            ('account_id', '=', self.company.cl_rice_machine_cost_account_id.id),
            ('parent_state', '=', 'posted')]).mapped('balance'))
        self.assertEqual(expense, 60_000, "only the fuel used is a cost")

    def test_reversal_reverses_the_cost(self):
        job = self._job()
        job.with_user(self.user_manager)._cl_reverse("Wrong machine")
        self.assertTrue(job.cost_move_id.reversal_move_ids)
        self.assertEqual(job.state, 'cancelled')

    # ------------------------------------------------------------------
    # G10: company-paid expenses are posted and in the cash book
    # ------------------------------------------------------------------
    def test_company_paid_expense_is_posted(self):
        employee = self.env.user.employee_id or self.env['hr.employee'].sudo().create(
            {'name': "Daudi Mkwawa", 'user_id': self.env.user.id})
        expense = self.env['hr.expense'].create({
            'name': "Spare part for the tractor",
            'employee_id': employee.id,
            'product_id': self.env.ref('cl_rice_mill.product_expense_repairs').id,
            'total_amount_currency': 45_000,
            'payment_mode': 'company_account',
            'payment_method_line_id': self.cash_journal._cl_method_line('outbound').id,
            'cl_vehicle_id': self.tractor.id,
        })
        self.assertIn(self.tractor.cl_analytic_account_id.id, self._accounts_of(expense.analytic_distribution))
        self.assertIn(self.project_account.id, self._accounts_of(expense.analytic_distribution))
        expense.action_submit()
        self.env.user.sudo().group_ids |= self.env.ref('hr_expense.group_hr_expense_manager')
        expense.action_approve()
        move = expense.account_move_id
        self.assertEqual(move.state, 'posted')
        book = self.env['cl.rice.report.cash.book'].search([('move_id', '=', move.id)])
        self.assertEqual(sum(book.mapped('money_out')), 45_000)

    # ------------------------------------------------------------------
    # G17: setup
    # ------------------------------------------------------------------
    def test_setup_cost_side(self):
        company = self.company
        for field in ('cl_rice_fuel_clearing_account_id', 'cl_rice_machine_cost_account_id',
                      'cl_rice_operator_wage_account_id', 'cl_rice_repair_account_id', 'cl_rice_bags_account_id',
                      'cl_rice_transport_account_id', 'cl_rice_airtime_account_id', 'cl_rice_office_account_id',
                      'cl_rice_cost_journal_id'):
            self.assertTrue(company[field], field)
        airtime = self.env.ref('cl_rice_mill.product_expense_airtime').with_company(company)
        self.assertEqual(airtime.property_account_expense_id, company.cl_rice_airtime_account_id)
        self.assertTrue(airtime.can_be_expensed)
        company._cl_rice_setup_documents()
        self.assertTrue(company.external_report_layout_id)
        self.env['res.company']._cl_rice_enable_analytic()
        self.assertIn(self.env.ref('analytic.group_analytic_accounting'), self.env.ref('base.group_user').implied_ids)
        self.assertTrue(self.user_accountant.has_group('analytic.group_analytic_accounting'))

    # ------------------------------------------------------------------
    # G18: staff trips
    # ------------------------------------------------------------------
    def test_trip_posting(self):
        trip = self.env['cl.rice.trip'].create({
            'employee_id': self.officer_employee.id, 'vehicle_id': self.motorbike.id, 'purpose': "Visit Igurusi farmers",
            'farmer_season_id': self.fs.id, 'odometer_start': 1200, 'odometer_end': 1260, 'fuel_litres': 2,
        })
        self.assertEqual(trip.km, 60)
        self.assertEqual(trip.fuel_cost, 6_000)
        trip.with_user(self.user_accountant).action_post()
        transport = trip.move_id.line_ids.filtered(lambda l: l.account_id == self.company.cl_rice_transport_account_id)
        self.assertEqual(transport.debit, 6_000)
        accounts = self._accounts_of(transport.analytic_distribution)
        self.assertTrue({self.motorbike.cl_analytic_account_id.id, self.fs.analytic_account_id.id,
                         self.project_account.id} <= accounts)
        self.motorbike.invalidate_recordset(['odometer'])
        self.assertEqual(self.motorbike.odometer, 1260)

    def test_trip_from_the_field_app(self):
        record = {'uuid': str(uuid.uuid4()), 'kind': 'trip',
                  'data': {'vehicle_id': self.motorbike.id, 'purpose': "Farmer visits", 'farmer_season_id': self.fs.id,
                           'odometer_start': '10', 'odometer_end': '35', 'fuel_litres': '1'}}
        Sync = self.env['cl.rice.field.sync'].with_user(self.user_officer)
        [first] = Sync._cl_sync([record])
        [again] = Sync._cl_sync([record])
        self.assertEqual(first['status'], 'synced')
        self.assertEqual(again['status'], 'duplicate')
        trip = self.env['cl.rice.trip'].browse(first['id'])
        self.assertEqual((trip.state, trip.source, trip.employee_id), ('draft', 'field', self.officer_employee))
        other = dict(record, uuid=str(uuid.uuid4()), data=dict(record['data'], farmer_season_id=self.fs_2.id))
        self.assertEqual(Sync._cl_sync([other])[0]['status'], 'failed')
        data = Sync._cl_bootstrap()
        self.assertIn(self.motorbike.id, [v['id'] for v in data['vehicles']])
