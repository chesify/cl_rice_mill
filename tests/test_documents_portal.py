from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestFarmerDocuments(ClRiceCommon):
    """G2: the farmer documents, printed with the company letterhead."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        cls.company._cl_rice_setup_documents()
        cls.farmer.sudo().cl_nida_number = '19800101-11111-00001-22'
        cls.loan = cls._disburse(cls._create_loan(cls.fs, 300_000, purpose="Land preparation"))
        cls.intake = cls._intake(cls.fs)
        cls.job = cls._mill(cls.fs)

    def _html(self, report, records, data=None, user=None):
        Report = self.env['ir.actions.report'].with_user(user or self.env.user)
        return Report._render_qweb_html(report, records.ids, data=data)[0].decode()

    def test_statement_lists_everything_with_a_running_balance(self):
        html = self._html('cl_rice_mill.action_report_farmer_statement', self.fs)
        self.assertIn("Farmer Account Statement", html)
        self.assertIn(f"Loan {self.loan.name} - land preparation", html)
        self.assertIn(f"Milling 1,000 kg of paddy ({self.job.name})", html)
        statement = self.fs._cl_statement()
        self.assertEqual(statement['charges'], 300_000 + 50_000)
        self.assertEqual(statement['lines'][-1]['balance'], statement['closing'])

    def test_statement_date_filter(self):
        today = date.today()
        statement = self.fs._cl_statement(date_from=date(today.year + 1, 1, 1))
        self.assertFalse(statement['lines'])
        self.assertEqual(statement['opening'], 350_000, "everything before the period is brought forward")
        wizard = self.env['cl.rice.statement.print'].with_context(
            active_model='res.partner', active_id=self.farmer.id).create({'date_from': today})
        self.assertEqual(wizard.farmer_season_id, self.fs)
        action = wizard.action_print()
        self.assertEqual(action['data']['date_from'], today.isoformat())
        html = self._html('cl_rice_mill.action_report_farmer_statement', self.fs, data=action['data'])
        self.assertIn("From", html)

    def test_documents_render(self):
        receipt = self._html('cl_rice_mill.action_report_warehouse_receipt', self.intake)
        self.assertIn("Warehouse Receipt", receipt)
        self.assertIn(self.intake.lot_id.name, receipt)
        agreement = self._html('cl_rice_mill.action_report_loan_agreement', self.loan, user=self.user_officer)
        self.assertIn('19800101-11111-00001-22', agreement, "printed although the officer cannot read the NIDA")
        self.assertIn("Witness", agreement)
        certificate = self._html('cl_rice_mill.action_report_milling_certificate', self.job)
        self.assertIn("Milling Certificate", certificate)
        self.assertIn("65.00", certificate)  # recovery %

    def test_settlement_statement_shows_the_full_breakdown(self):
        self._receive(self.fs, 400_000)
        action = self.fs.with_user(self.user_accountant).action_open_settle()
        wizard = self.env['cl.rice.settlement.create'].with_user(self.user_accountant).with_context(
            action['context']).create({'payout_journal_id': self.cash_journal.id})
        settlement = self.env['cl.rice.settlement'].browse(wizard.action_create()['res_id'])
        html = self._html('cl_rice_mill.action_report_settlement_statement', settlement)
        self.assertIn(f"Loan {self.loan.name}", html)
        self.assertIn("Everything of the season", html)

    def test_grant_portal_access(self):
        farmer = self.env['res.partner'].create({'name': "Portal Farmer", 'is_farmer': True, 'phone': '0754 111 222'})
        with self.assertRaises(AccessError):
            farmer.with_user(self.user_officer).action_cl_grant_portal()
        result = farmer.with_user(self.user_accountant).action_cl_grant_portal()
        user = farmer.user_ids
        self.assertEqual(user.login, '255754111222')
        self.assertTrue(user._is_portal())
        self.assertIn('255754111222', result['params']['message'])
        with self.assertRaises(UserError):
            farmer.with_user(self.user_accountant).action_cl_grant_portal()


@tagged('post_install', '-at_install')
class TestFarmerPortalHttp(HttpCase):
    """G3: a farmer only ever sees their own data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company._cl_rice_setup_company()
        season = cls.env['cl.rice.season'].create({
            'name': "Season Portal", 'code': 'PRTL', 'date_start': '2026-07-01', 'date_end': '2027-06-30'})
        cls.fss = cls.env['cl.rice.farmer.season']
        cls.users = []
        for name in ("Alpha", "Beta"):
            farmer = cls.env['res.partner'].create({'name': f"Farmer {name}", 'is_farmer': True})
            fs = cls.env['cl.rice.farmer.season'].create({'partner_id': farmer.id, 'season_id': season.id})
            fs.action_activate()
            cls.fss |= fs
            login = f'rm_portal_{name.lower()}'
            new_test_user(cls.env, login=login, password=login, partner_id=farmer.id,
                          group_ids=[cls.env.ref('base.group_portal').id])
            cls.users.append(login)
        cls.intake = cls.env['cl.rice.intake'].create({
            'farmer_season_id': cls.fss[0].id, 'bags': 10, 'gross_weight': 905})
        cls.intake.action_confirm()

    def test_orm_isolation(self):
        alpha = self.env['res.users'].search([('login', '=', 'rm_portal_alpha')])
        FarmerSeason = self.env['cl.rice.farmer.season'].with_user(alpha)
        self.assertEqual(FarmerSeason.search([]), self.fss[0])
        with self.assertRaises(AccessError):
            self.fss[1].with_user(alpha).check_access('read')
        self.assertEqual(self.env['cl.rice.intake'].with_user(alpha).search([]), self.intake)
        beta = self.env['res.users'].search([('login', '=', 'rm_portal_beta')])
        self.assertFalse(self.env['cl.rice.intake'].with_user(beta).search([]))
        with self.assertRaises(AccessError):
            self.intake.with_user(beta).write({'note': "x"})

    def test_pages(self):
        alpha, beta = self.fss
        self.authenticate('rm_portal_alpha', 'rm_portal_alpha')
        home = self.url_open('/my/rice')
        self.assertEqual(home.status_code, 200)
        self.assertIn(alpha.name, home.text)
        self.assertNotIn(beta.name, home.text)
        page = self.url_open(f'/my/rice/{alpha.id}')
        self.assertIn("Paddy received", page.text)
        self.assertIn("Paddy received: 900 kg in 10 bags", page.text)
        statement = self.url_open(f'/my/rice/{alpha.id}/statement')
        self.assertEqual(statement.status_code, 200)
        pdf = self.url_open(f'/my/rice/{alpha.id}/statement?download=1')
        self.assertEqual(pdf.headers['Content-Type'], 'application/pdf')
        receipt = self.url_open(f'/my/rice/document/receipt/{self.intake.id}')
        self.assertEqual(receipt.headers['Content-Type'], 'application/pdf')
        # another farmer's season and documents are never shown
        other = self.url_open(f'/my/rice/{beta.id}')
        self.assertNotIn(beta.name, other.text)
        self.authenticate('rm_portal_beta', 'rm_portal_beta')
        stolen = self.url_open(f'/my/rice/document/receipt/{self.intake.id}')
        self.assertNotEqual(stolen.headers.get('Content-Type'), 'application/pdf')
        stolen_statement = self.url_open(f'/my/rice/{alpha.id}/statement?download=1')
        self.assertNotEqual(stolen_statement.headers.get('Content-Type'), 'application/pdf')

    def test_request_creates_a_staff_activity(self):
        alpha = self.fss[0]
        self.authenticate('rm_portal_alpha', 'rm_portal_alpha')
        page = self.url_open(f'/my/rice/{alpha.id}')
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        response = self.url_open(f'/my/rice/{alpha.id}/request', data={
            'csrf_token': token, 'request_type': 'service', 'message': "Tractor for 3 acres next week"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("request was sent", response.text)
        activity = alpha.activity_ids.filtered(lambda a: 'Service request' in (a.summary or ''))
        self.assertTrue(activity)
