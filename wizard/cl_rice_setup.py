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
    currency_id = fields.Many2one(related='company_id.currency_id')
    fuel_price = fields.Monetary(
        string="Fuel Price per Litre", default=lambda self: self.env.company.cl_rice_fuel_price,
        help="Values the fuel used by service jobs and trips.")
    logo = fields.Binary(string="Company Logo", help="Printed on receipts, statements and gate passes.")
    bran_price = fields.Monetary(
        string="Bran Sale Price (per kg)", default=lambda self: self._cl_product_value('bran', 'list_price'))
    bran_cost = fields.Monetary(
        string="Bran Cost (per kg)", default=lambda self: self._cl_product_value('bran', 'standard_price'),
        help="Values the bran the mill keeps: stock already held is revalued.")
    husk_price = fields.Monetary(
        string="Husk Sale Price (per kg)", default=lambda self: self._cl_product_value('husk', 'list_price'))
    husk_cost = fields.Monetary(
        string="Husk Cost (per kg)", default=lambda self: self._cl_product_value('husk', 'standard_price'))
    archive_default_warehouse = fields.Boolean(
        string="Archive the Unused Default Warehouse",
        help="Archive the company's other warehouse (\"WH\") when it holds no stock, so that every operation "
             "uses the rice mill warehouse.")
    summary = fields.Html(readonly=True)

    def _cl_product_value(self, role, field_name):
        product = self.env.ref(f'cl_rice_mill.product_{role}', raise_if_not_found=False)
        return product.with_company(self.env.company)[field_name] if product else 0.0

    def _cl_set_byproduct_prices(self, company):
        for role in ('bran', 'husk'):
            product = self.env.ref(f'cl_rice_mill.product_{role}').with_company(company).sudo()
            vals = {}
            if self[f'{role}_price'] and product.list_price != self[f'{role}_price']:
                vals['list_price'] = self[f'{role}_price']
            if self[f'{role}_cost'] and product.standard_price != self[f'{role}_cost']:
                vals['standard_price'] = self[f'{role}_cost']  # revalues the stock held
            if vals:
                product.write(vals)

    def _cl_archive_default_warehouses(self, company):
        """Archive the company's other warehouses when nothing is stored or moving there."""
        Warehouse = self.env['stock.warehouse'].sudo()
        archived = Warehouse
        for warehouse in Warehouse.search([
                ('company_id', '=', company.id), ('id', '!=', company.cl_rice_warehouse_id.id)]):
            stock = self.env['stock.quant'].sudo().search_count(
                [('location_id', 'child_of', warehouse.view_location_id.id), ('quantity', '!=', 0)], limit=1)
            moving = self.env['stock.picking'].sudo().search_count([
                ('picking_type_id.warehouse_id', '=', warehouse.id),
                ('state', 'not in', ('done', 'cancel'))], limit=1)
            if not stock and not moving:
                warehouse.active = False
                archived |= warehouse
        return archived

    def action_apply(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._("Only an administrator can run the Rice Mill setup."))
        company = self.company_id
        if not company.chart_template:
            raise UserError(self.env._("Install a chart of accounts for %s first (Accounting settings).", company.name))
        company._cl_rice_setup_company()
        company.write({'cl_rice_storage_enabled': self.storage_enabled, 'cl_rice_fuel_price': self.fuel_price})
        if self.logo:
            company.logo = self.logo
        company._cl_rice_setup_documents()
        self._cl_set_byproduct_prices(company)
        if self.archive_default_warehouse:
            self._cl_archive_default_warehouses(company)
        self.env['res.company']._cl_rice_enable_analytic()
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
