from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain

# (code, name, type)
PAYMENT_JOURNALS = [
    ('CSH', "Cash", 'cash'),
    ('BNK', "Bank", 'bank'),
    ('MPS', "M-Pesa", 'bank'),
    ('TGP', "Tigo Pesa", 'bank'),
    ('ATM', "Airtel Money", 'bank'),
]


class ClRiceSetup(models.TransientModel):
    _name = 'cl.rice.setup'
    _description = "Rice Mill Setup"

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    create_payment_journals = fields.Boolean(
        string="Cash, Bank and Mobile Money Journals", default=True,
        help="Create the Cash, Bank, M-Pesa, Tigo Pesa and Airtel Money journals that are missing.")
    storage_enabled = fields.Boolean(
        string="Charge Storage Fees", default=lambda self: self.env.company.cl_rice_storage_enabled)
    summary = fields.Html(readonly=True)

    def action_apply(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._("Only an administrator can run the Rice Mill setup."))
        company = self.company_id
        if not company.chart_template:
            raise UserError(self.env._("Install a chart of accounts for %s first (Accounting settings).", company.name))
        company._cl_rice_setup_company()
        company.cl_rice_storage_enabled = self.storage_enabled
        journals = self.env['account.journal']
        if self.create_payment_journals:
            journals = self._cl_create_payment_journals(company)
        self._cl_set_outstanding_accounts(
            company, journals | self.env['account.journal'].sudo().search(
                [('company_id', '=', company.id), ('type', 'in', ('cash', 'bank'))]))
        self.env['res.partner'].sudo().search([('is_farmer', '=', True)])._cl_set_farmer_accounts(company)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._("Rice Mill Setup"),
                'message': self.env._("Accounts, journals and warehouse partitions are ready."),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _cl_create_payment_journals(self, company):
        Journal = self.env['account.journal'].sudo().with_company(company)
        journals = Journal
        for code, name, journal_type in PAYMENT_JOURNALS:
            journal = Journal.search(
                Domain(Journal._check_company_domain(company)) & (Domain('code', '=', code) | Domain('name', '=', name)),
                limit=1)
            if not journal:
                journal = Journal.create({'code': code, 'name': name, 'type': journal_type, 'company_id': company.id})
            journals |= journal
        return journals

    def _cl_set_outstanding_accounts(self, company, journals):
        """Give every payment method an outstanding account, so payments are booked at once."""
        ChartTemplate = self.env['account.chart.template'].with_company(company)
        Account = self.env['account.account'].sudo().with_company(company)
        for payment_type, xml_id, code, name in (
            ('inbound', 'account_journal_payment_debit_account_id', 'RM1210', "Outstanding Receipts"),
            ('outbound', 'account_journal_payment_credit_account_id', 'RM1220', "Outstanding Payments"),
        ):
            account = ChartTemplate.ref(xml_id, raise_if_not_found=False)
            if not account:
                account = Account.search(
                    Domain(Account._check_company_domain(company)) & Domain('code', '=', code), limit=1)
            if not account:
                account = Account.create({
                    'code': code, 'name': name, 'account_type': 'asset_current', 'reconcile': True,
                    'company_ids': [(6, 0, company.ids)],
                })
            lines = journals[f'{payment_type}_payment_method_line_ids'].filtered(lambda l: not l.payment_account_id)
            lines.sudo().payment_account_id = account
