from odoo import api, fields, models
from odoo.fields import Domain

# (field name, code, name, account_type, reconcile)
RICE_ACCOUNTS = [
    ('cl_rice_loan_account_id', 'RM1310', "Farmer Loans Receivable", 'asset_receivable', True),
    ('cl_rice_service_account_id', 'RM1320', "Farmer Service Receivable", 'asset_receivable', True),
    ('cl_rice_proceeds_account_id', 'RM2310', "Farmer Proceeds Payable", 'liability_payable', True),
    ('cl_rice_clearing_account_id', 'RM2320', "Farmer Sales Clearing", 'liability_current', False),
    ('cl_rice_interest_income_account_id', 'RM4310', "Farmer Loan Interest Income", 'income_other', False),
    ('cl_rice_tractor_income_account_id', 'RM4320', "Tractor Services Income", 'income', False),
    ('cl_rice_harvester_income_account_id', 'RM4330', "Harvester Services Income", 'income', False),
    ('cl_rice_storage_income_account_id', 'RM4340', "Storage Income", 'income', False),
    ('cl_rice_milling_income_account_id', 'RM4350', "Milling Income", 'income', False),
    ('cl_rice_commission_income_account_id', 'RM4360', "Sales Commission Income", 'income', False),
    ('cl_rice_byproduct_income_account_id', 'RM4370', "By-products Income", 'income', False),
    ('cl_rice_other_income_account_id', 'RM4380', "Other Farmer Services Income", 'income', False),
]

# (field name, code, name, type)
RICE_JOURNALS = [
    ('cl_rice_charge_journal_id', 'RMCH', "Farmer Charges", 'sale'),
    ('cl_rice_walkin_journal_id', 'RMWI', "Walk-in Milling", 'sale'),
    ('cl_rice_loan_journal_id', 'RMLN', "Farmer Loans", 'general'),
    ('cl_rice_settlement_journal_id', 'RMST', "Farmer Settlements", 'general'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Loans and interest
    cl_rice_interest_mode = fields.Selection(
        [('none', "No interest"), ('flat', "Flat % per loan"), ('monthly', "Simple monthly %")],
        string="Interest Mode", default='none', required=True)
    cl_rice_interest_rate = fields.Float(
        string="Interest Rate (%)", digits=(16, 4),
        help="Flat mode: percentage of the loan amount. Monthly mode: percentage per started month.")
    cl_rice_interest_accrual = fields.Selection(
        [('cron', "Accrue monthly (scheduled)"), ('settlement', "Calculate at settlement")],
        string="Monthly Interest Accrual", default='settlement', required=True)
    cl_rice_ltv_percent = fields.Float(
        string="Loan-to-Value (%)", default=50.0,
        help="Share of the expected crop value that can be lent to a farmer.")
    cl_rice_default_yield = fields.Float(
        string="Expected Yield (kg/acre)", default=1200.0, digits='Product Unit')
    cl_rice_min_cover_percent = fields.Float(
        string="Minimum Collateral Cover (%)", default=120.0,
        help="Warn when the value of the stock held is below this share of the amount owed.")

    # Sales
    cl_rice_sales_mode = fields.Selection(
        [('agent', "Agent (sell on behalf of the farmer)"), ('owner', "Owner (buy from the farmer, then resell)")],
        string="Sales Mode", default='agent', required=True)
    cl_rice_commission_type = fields.Selection(
        [('percent', "Percentage of the sale"), ('per_kg', "Fixed fee per kg")],
        string="Commission Type", default='percent', required=True)
    cl_rice_commission_value = fields.Float(string="Commission", digits=(16, 4), default=5.0)

    # By-products policy
    cl_rice_bran_policy = fields.Selection(
        [('farmer', "Goes to the farmer"), ('kept', "Kept by the mill")],
        string="Bran (Pumba)", default='kept', required=True)
    cl_rice_bran_value = fields.Monetary(string="Bran Credit per kg", currency_field='currency_id')
    cl_rice_husk_policy = fields.Selection(
        [('farmer', "Goes to the farmer"), ('kept', "Kept by the mill")],
        string="Husk (Makapi)", default='kept', required=True)
    cl_rice_husk_value = fields.Monetary(string="Husk Credit per kg", currency_field='currency_id')
    cl_rice_broken_policy = fields.Selection(
        [('farmer', "Goes to the farmer"), ('kept', "Kept by the mill")],
        string="Broken Rice", default='farmer', required=True)
    cl_rice_broken_value = fields.Monetary(string="Broken Rice Credit per kg", currency_field='currency_id')

    # Storage
    cl_rice_storage_basis = fields.Selection(
        [('bag', "Per bag per month"), ('kg', "Per kg per month")],
        string="Storage Fee Basis", default='bag', required=True)
    cl_rice_storage_rate = fields.Monetary(string="Storage Fee Rate", currency_field='currency_id')
    cl_rice_storage_free_days = fields.Integer(string="Free Storage (days)", default=30)
    cl_rice_paddy_bag_kg = fields.Float(string="Standard Paddy Bag (kg)", default=90.0)
    cl_rice_rice_bag_kg = fields.Float(string="Standard Rice Bag (kg)", default=50.0)

    # Milling cost split for mill-owned paddy (relative value per kg of each output)
    cl_rice_split_rice = fields.Float(string="Rice Value Weight", default=1.0)
    cl_rice_split_broken = fields.Float(string="Broken Rice Value Weight", default=0.6)
    cl_rice_split_bran = fields.Float(string="Bran Value Weight", default=0.15)
    cl_rice_split_husk = fields.Float(string="Husk Value Weight", default=0.0)

    # Accounts
    cl_rice_loan_account_id = fields.Many2one('account.account', string="Farmer Loans Receivable")
    cl_rice_service_account_id = fields.Many2one('account.account', string="Farmer Service Receivable")
    cl_rice_proceeds_account_id = fields.Many2one('account.account', string="Farmer Proceeds Payable")
    cl_rice_clearing_account_id = fields.Many2one('account.account', string="Farmer Sales Clearing")
    cl_rice_interest_income_account_id = fields.Many2one('account.account', string="Interest Income")
    cl_rice_tractor_income_account_id = fields.Many2one('account.account', string="Tractor Income")
    cl_rice_harvester_income_account_id = fields.Many2one('account.account', string="Harvester Income")
    cl_rice_storage_income_account_id = fields.Many2one('account.account', string="Storage Income")
    cl_rice_milling_income_account_id = fields.Many2one('account.account', string="Milling Income")
    cl_rice_commission_income_account_id = fields.Many2one('account.account', string="Commission Income")
    cl_rice_byproduct_income_account_id = fields.Many2one('account.account', string="By-products Income")
    cl_rice_other_income_account_id = fields.Many2one('account.account', string="Other Services Income")

    # Stock
    cl_rice_paddy_warehouse_id = fields.Many2one('stock.warehouse', string="Paddy Store (W1)")
    cl_rice_rice_warehouse_id = fields.Many2one('stock.warehouse', string="Rice Store (W2)")
    cl_rice_mill_location_id = fields.Many2one('stock.location', string="Mill Location")
    cl_rice_consume_type_id = fields.Many2one('stock.picking.type', string="Mill Consumption Operation")
    cl_rice_output_type_id = fields.Many2one('stock.picking.type', string="Mill Output Operation")
    cl_rice_bag_tare = fields.Float(string="Tare per Bag (kg)", default=0.5, digits='Product Unit')

    # Journals
    cl_rice_charge_journal_id = fields.Many2one('account.journal', string="Farmer Charges Journal")
    cl_rice_walkin_journal_id = fields.Many2one('account.journal', string="Walk-in Milling Journal")
    cl_rice_loan_journal_id = fields.Many2one('account.journal', string="Farmer Loans Journal")
    cl_rice_settlement_journal_id = fields.Many2one('account.journal', string="Settlement Journal")

    @api.model
    def _cl_rice_account_specs(self):
        return RICE_ACCOUNTS

    @api.model
    def _cl_rice_journal_specs(self):
        return RICE_JOURNALS

    def _cl_rice_setup_company(self):
        """Create the rice mill accounts, journals and stock setup the companies still miss.

        Safe to call several times: only empty configuration fields are filled.
        """
        for company in self:
            company._cl_rice_setup_stock()
            if not company.chart_template:
                # no chart of accounts yet: the setup is run again from the settings
                continue
            company._cl_rice_setup_accounting()

    def _cl_rice_setup_stock(self):
        self.ensure_one()
        category = self.env.ref('cl_rice_mill.product_category_rice_mill', raise_if_not_found=False)
        if category:
            # crop bought from farmers is valued at the price paid
            category.with_company(self).sudo().property_cost_method = 'average'
        Warehouse = self.env['stock.warehouse'].sudo()
        vals = {}
        for field_name, code, name in (
            ('cl_rice_paddy_warehouse_id', 'GMP', "Ghala la Mpunga (Paddy Store)"),
            ('cl_rice_rice_warehouse_id', 'GMC', "Ghala la Mchele (Rice Store)"),
        ):
            if self[field_name]:
                continue
            warehouse = Warehouse.search([('company_id', '=', self.id), ('code', '=', code)], limit=1)
            if not warehouse:
                warehouse = Warehouse.create({'name': name, 'code': code, 'company_id': self.id})
            vals[field_name] = warehouse.id
        if not self.cl_rice_mill_location_id:
            vals['cl_rice_mill_location_id'] = self.env['stock.location'].sudo().create({
                'name': "Kinu (Rice Mill)",
                'usage': 'production',
                'company_id': self.id,
            }).id
        if vals:
            self.sudo().write(vals)
        vals = {}
        PickingType = self.env['stock.picking.type'].sudo()
        if not self.cl_rice_consume_type_id:
            vals['cl_rice_consume_type_id'] = PickingType.create({
                'name': "Milling Consumption",
                'code': 'internal',
                'sequence_code': 'MLC',
                'warehouse_id': self.cl_rice_paddy_warehouse_id.id,
                'default_location_src_id': self.cl_rice_paddy_warehouse_id.lot_stock_id.id,
                'default_location_dest_id': self.cl_rice_mill_location_id.id,
                'company_id': self.id,
            }).id
        if not self.cl_rice_output_type_id:
            vals['cl_rice_output_type_id'] = PickingType.create({
                'name': "Milling Output",
                'code': 'internal',
                'sequence_code': 'MLO',
                'warehouse_id': self.cl_rice_rice_warehouse_id.id,
                'default_location_src_id': self.cl_rice_mill_location_id.id,
                'default_location_dest_id': self.cl_rice_rice_warehouse_id.lot_stock_id.id,
                'company_id': self.id,
            }).id
        if vals:
            self.sudo().write(vals)

    def _cl_rice_store_locations(self):
        """Internal locations of the paddy and rice stores."""
        self.ensure_one()
        return (self.cl_rice_paddy_warehouse_id | self.cl_rice_rice_warehouse_id).lot_stock_id

    def _cl_rice_setup_accounting(self):
        self.ensure_one()
        Account = self.env['account.account'].sudo().with_company(self)
        vals = {}
        for field_name, code, name, account_type, reconcile in self._cl_rice_account_specs():
            if self[field_name]:
                continue
            account = Account.search(
                Domain(Account._check_company_domain(self)) & Domain('code', '=', code), limit=1)
            if not account:
                account = Account.create({
                    'code': code,
                    'name': name,
                    'account_type': account_type,
                    'reconcile': reconcile,
                    'company_ids': [(6, 0, self.ids)],
                })
            vals[field_name] = account.id
        Journal = self.env['account.journal'].sudo().with_company(self)
        for field_name, code, name, journal_type in self._cl_rice_journal_specs():
            if self[field_name]:
                continue
            journal = Journal.search(
                Domain(Journal._check_company_domain(self)) & Domain('code', '=', code), limit=1)
            if not journal:
                journal = Journal.create({
                    'code': code,
                    'name': name,
                    'type': journal_type,
                    'company_id': self.id,
                })
            vals[field_name] = journal.id
        if vals:
            self.sudo().write(vals)
        self._cl_rice_setup_product_accounts()

    def _cl_rice_setup_product_accounts(self):
        """By-products are sold to the by-products income account."""
        self.ensure_one()
        if not self.cl_rice_byproduct_income_account_id:
            return
        for xml_id in ('product_broken', 'product_bran', 'product_husk'):
            product = self.env.ref(f'cl_rice_mill.{xml_id}', raise_if_not_found=False)
            template = product and product.product_tmpl_id.with_company(self).sudo()
            if template and not template.property_account_income_id:
                template.property_account_income_id = self.cl_rice_byproduct_income_account_id
