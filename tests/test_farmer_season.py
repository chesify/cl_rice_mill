from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestFarmerSeason(ClRiceCommon):

    def test_company_setup_is_idempotent(self):
        company = self.company
        self.assertTrue(company.cl_rice_loan_account_id)
        self.assertEqual(company.cl_rice_loan_account_id.account_type, 'asset_receivable')
        self.assertTrue(company.cl_rice_loan_account_id.reconcile)
        self.assertEqual(company.cl_rice_charge_journal_id.type, 'sale')
        accounts_before = self.env['account.account'].search_count([])
        journals_before = self.env['account.journal'].search_count([])
        company._cl_rice_setup_company()
        self.assertEqual(self.env['account.account'].search_count([]), accounts_before)
        self.assertEqual(self.env['account.journal'].search_count([]), journals_before)

    def test_farmer_registration(self):
        self.assertTrue(self.farmer.cl_farmer_code.startswith('F'))
        self.assertNotEqual(self.farmer.cl_farmer_code, self.farmer_2.cl_farmer_code)
        self.assertEqual(self.farmer.lang, 'sw')
        self.assertEqual(
            self.farmer.with_company(self.company).property_account_receivable_id,
            self.company.cl_rice_service_account_id)
        found = self.env['res.partner'].search([('display_name', 'ilike', self.farmer.cl_farmer_code)])
        self.assertIn(self.farmer, found)

    def test_season_and_farmer_season_links(self):
        self.assertTrue(self.season.project_id)
        self.assertEqual(self.season.analytic_plan_id.parent_id, self.env.ref('cl_rice_mill.analytic_plan_farmer_seasons'))
        self.assertEqual(self.fs.analytic_account_id.plan_id, self.season.analytic_plan_id)
        self.assertEqual(self.fs.analytic_account_id.partner_id, self.farmer)
        self.assertEqual(self.fs.task_id.project_id, self.season.project_id)
        self.assertEqual(self.fs.field_officer_id, self.user_officer)
        self.assertEqual(self.season.farmer_count, 2)

    def test_one_farmer_season_per_season(self):
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self.env['cl.rice.farmer.season'].create({
                'partner_id': self.farmer.id,
                'season_id': self.season.id,
            })

    def test_credit_limit(self):
        # 4 acres x 1000 kg x 1000 TZS x 50%
        self.assertEqual(self.fs.credit_limit_computed, 2_000_000)
        self.assertEqual(self.fs.credit_limit, 2_000_000)

    def test_credit_override_requires_manager(self):
        wizard_vals = {
            'res_model': 'cl.rice.farmer.season',
            'res_id': self.fs.id,
            'action': 'credit_override',
            'amount': 3_000_000,
            'reason': "Good repayment history",
        }
        wizard = self.env['cl.rice.reason'].with_user(self.user_accountant).create(wizard_vals)
        with self.assertRaises(AccessError):
            wizard.action_confirm()
        self.env['cl.rice.reason'].with_user(self.user_manager).create(wizard_vals).action_confirm()
        self.assertEqual(self.fs.credit_limit, 3_000_000)
        self.assertEqual(self.fs.credit_limit_override_uid, self.user_manager)
        self.assertIn("Good repayment history", self.fs.message_ids[0].body)

    def test_reason_wizard_rejects_unknown_targets(self):
        wizard = self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'res.partner',
            'res_id': self.farmer.id,
            'action': 'credit_override',
            'reason': "x",
        })
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_locked_farmer_season(self):
        self.fs.with_context(cl_rice_unlock=True).state = 'settled'
        self.assertTrue(self.fs.is_locked)
        with self.assertRaises(UserError):
            self.fs.acreage = 10
        with self.assertRaises(UserError):
            self.fs._cl_check_open()
        # notes and chatter stay available
        self.fs.note = "<p>Settled in full</p>"
        self.fs.action_close()
        self.assertEqual(self.fs.state, 'closed')


@tagged('post_install', '-at_install')
class TestFarmerSecurity(ClRiceCommon):

    def test_field_officer_isolation(self):
        FarmerSeason = self.env['cl.rice.farmer.season'].with_user(self.user_officer)
        self.assertEqual(FarmerSeason.search([]), self.fs)
        with self.assertRaises(AccessError):
            self.fs_2.with_user(self.user_officer).read(['acreage'])
        with self.assertRaises(AccessError):
            self.fs_2.with_user(self.user_officer).write({'acreage': 1})

        Partner = self.env['res.partner'].with_user(self.user_officer)
        farmers = Partner.search([('is_farmer', '=', True)])
        self.assertIn(self.farmer, farmers)
        self.assertNotIn(self.farmer_2, farmers)
        # ordinary contacts stay visible
        self.assertIn(self.partner_a, Partner.search([('id', '=', self.partner_a.id)]))
        # a field officer cannot register a farmer for another officer
        with self.assertRaises(AccessError):
            Partner.create({'name': "Mgeni", 'is_farmer': True, 'cl_field_officer_id': self.user_officer_2.id})

    def test_field_officer_registers_farmer(self):
        farmer = self.env['res.partner'].with_user(self.user_officer).create({'name': "Asha Mbwilo", 'is_farmer': True})
        self.assertEqual(farmer.cl_field_officer_id, self.user_officer)
        fs = self.env['cl.rice.farmer.season'].with_user(self.user_officer).create({
            'partner_id': farmer.id,
            'season_id': self.season.id,
            'acreage': 1.5,
        })
        self.assertEqual(fs.field_officer_id, self.user_officer)

    def test_other_roles_see_all_farmers(self):
        for user in (self.user_warehouse, self.user_miller, self.user_accountant, self.user_manager):
            seasons = self.env['cl.rice.farmer.season'].with_user(user).search([])
            self.assertEqual(seasons, self.fs | self.fs_2, user.login)
            farmers = self.env['res.partner'].with_user(user).search([('is_farmer', '=', True)])
            self.assertIn(self.farmer_2, farmers, user.login)

    def test_sensitive_fields(self):
        self.farmer.cl_nida_number = '19800101-12345-00001-12'
        with self.assertRaises(AccessError):
            self.farmer.with_user(self.user_officer).read(['cl_nida_number'])
        with self.assertRaises(AccessError):
            self.farmer.with_user(self.user_warehouse).read(['cl_payout_phone'])
        self.assertTrue(self.farmer.with_user(self.user_accountant).cl_nida_number)

    def test_only_manager_changes_seasons(self):
        with self.assertRaises(AccessError):
            self.season.with_user(self.user_accountant).write({'reference_price': 1})
        self.season.with_user(self.user_manager).write({'reference_price': 1100})
