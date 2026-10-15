import re

from odoo.tests import tagged

from .common import ClRiceCommon

REMOVED_WORDS = ('interest', 'commission', 'proceeds', 'clearing', 'sales_mode', 'split', 'bran_value',
                 'husk_value', 'broken_value', 'bran_policy', 'husk_policy', 'paddy_warehouse', 'rice_rice_warehouse',
                 'byproduct_credit', 'farmer_price', 'ownership')


@tagged('post_install', '-at_install')
class TestRemovedFeatures(ClRiceCommon):
    """No interest, commission or agent/owner sale code, field, account or setting remains."""

    def test_removed_models(self):
        for model in ('cl.rice.loan.interest', 'cl.rice.proceeds', 'cl.rice.ownership.transfer'):
            self.assertNotIn(model, self.env.registry)
            self.assertFalse(self.env['ir.model'].search([('model', '=', model)]))

    def test_removed_fields(self):
        checks = {
            'res.company': 'cl_rice_',
            'res.config.settings': 'cl_rice_',
            'cl.rice.loan': '',
            'cl.rice.farmer.season': '',
            'cl.rice.milling.job': '',
            'account.move': 'cl_',
            'account.move.line': 'cl_',
            'sale.order.line': 'cl_',
        }
        for model, prefix in checks.items():
            names = [name for name in self.env[model]._fields if name.startswith(prefix)]
            leftovers = [name for name in names if any(word in name for word in REMOVED_WORDS)]
            self.assertFalse(leftovers, f"{model} still has {leftovers}")
        self.assertFalse([name for name in self.env['sale.order.line']._fields if name.startswith('cl_')])
        self.assertNotIn('mill', dict(self.env['cl.rice.milling.job']._fields['mode'].selection))

    def test_no_obsolete_accounts_or_data(self):
        codes = self.env['account.account'].with_context(active_test=False).search([
            ('company_ids', 'in', self.company.ids)]).mapped('code')
        for code in ('RM4310', 'RM2310', 'RM2320', 'RM4360'):
            self.assertNotIn(code, codes)
        for xmlid in ('analytic_service_commission', 'analytic_service_interest', 'ir_cron_cl_rice_accrue_interest',
                      'seq_cl_rice_ownership_transfer'):
            self.assertFalse(self.env.ref(f'cl_rice_mill.{xmlid}', raise_if_not_found=False), xmlid)
        accounts = self.company.cl_rice_funds_held_account_id | self.company.cl_rice_loan_account_id
        self.assertEqual(self.company.cl_rice_funds_held_account_id.account_type, 'liability_payable')
        self.assertEqual(len(accounts), 2)

    def test_settings_view_has_no_removed_setting(self):
        arch = self.env.ref('cl_rice_mill.res_config_settings_view_form').arch_db
        field_names = re.findall(r'name="(cl_rice_[a-z_]+)"', arch)
        self.assertTrue(field_names)
        for name in field_names:
            self.assertFalse(any(word in name for word in REMOVED_WORDS), name)
