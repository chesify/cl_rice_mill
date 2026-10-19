from odoo import api, fields, models
from odoo.fields import Domain

# (field name, code, name, account_type, reconcile)
RICE_ACCOUNTS = [
    ('cl_rice_loan_account_id', 'RM1310', "Farmer Loans Receivable", 'asset_receivable', True),
    ('cl_rice_service_account_id', 'RM1320', "Farmer Service Receivable", 'asset_receivable', True),
    ('cl_rice_funds_held_account_id', 'RM2330', "Farmer Funds Held", 'liability_payable', True),
    ('cl_rice_tractor_income_account_id', 'RM4320', "Tractor Services Income", 'income', False),
    ('cl_rice_harvester_income_account_id', 'RM4330', "Harvester Services Income", 'income', False),
    ('cl_rice_storage_income_account_id', 'RM4340', "Storage Income", 'income', False),
    ('cl_rice_milling_income_account_id', 'RM4350', "Milling Income", 'income', False),
    ('cl_rice_byproduct_income_account_id', 'RM4370', "By-products Sales Income", 'income', False),
    ('cl_rice_other_income_account_id', 'RM4380', "Other Farmer Services Income", 'income', False),
    ('cl_rice_fuel_clearing_account_id', 'RM1410', "Fuel Clearing", 'asset_current', False),
    ('cl_rice_operator_accrual_account_id', 'RM2410', "Accrued Operator Wages", 'liability_current', False),
    ('cl_rice_machine_cost_account_id', 'RM5110', "Machine Running Cost (Fuel)", 'expense_direct_cost', False),
    ('cl_rice_operator_wage_account_id', 'RM5120', "Operator Wages", 'expense_direct_cost', False),
    ('cl_rice_repair_account_id', 'RM5130', "Spare Parts and Repairs", 'expense', False),
    ('cl_rice_bags_account_id', 'RM5140', "Bags and Twine", 'expense', False),
    ('cl_rice_transport_account_id', 'RM5150', "Staff Field Transport", 'expense', False),
    ('cl_rice_airtime_account_id', 'RM5160', "Airtime and Communication", 'expense', False),
    ('cl_rice_office_account_id', 'RM5170', "Office and Warehouse Costs", 'expense', False),
]

# expense product xml id -> company account field used as its expense account
EXPENSE_PRODUCTS = [
    ('product_expense_diesel', 'cl_rice_fuel_clearing_account_id'),
    ('product_expense_repairs', 'cl_rice_repair_account_id'),
    ('product_expense_operator_wages', 'cl_rice_operator_accrual_account_id'),
    ('product_expense_bags', 'cl_rice_bags_account_id'),
    ('product_expense_transport', 'cl_rice_transport_account_id'),
    ('product_expense_airtime', 'cl_rice_airtime_account_id'),
    ('product_expense_office', 'cl_rice_office_account_id'),
]

# (field name, code, name, type)
RICE_JOURNALS = [
    ('cl_rice_charge_journal_id', 'RMCH', "Farmer Charges", 'sale'),
    ('cl_rice_walkin_journal_id', 'RMWI', "Walk-in Milling", 'sale'),
    ('cl_rice_loan_journal_id', 'RMLN', "Farmer Accounts", 'general'),
    ('cl_rice_settlement_journal_id', 'RMST', "Farmer Settlements", 'general'),
    ('cl_rice_cost_journal_id', 'RMMC', "Machine and Field Costs", 'general'),
]

# (field name, name)
RICE_LOCATIONS = [
    ('cl_rice_paddy_location_id', "Paddy"),
    ('cl_rice_rice_location_id', "Milled Rice"),
    ('cl_rice_byproduct_location_id', "By-products"),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Credit
    cl_rice_ltv_percent = fields.Float(
        string="Loan-to-Value (%)", default=50.0,
        help="Share of the expected crop value that can be lent to a farmer.")
    cl_rice_default_yield = fields.Float(
        string="Expected Yield (kg/acre)", default=1200.0, digits='Product Unit')
    cl_rice_min_cover_percent = fields.Float(
        string="Minimum Collateral Cover (%)", default=120.0,
        help="Warn when the value of the stock held is below this share of the amount owed.")

    # Milling and by-products
    cl_rice_broken_policy = fields.Selection(
        [('farmer', "Goes to the farmer"), ('kept', "Kept by the mill")],
        string="Broken Rice", default='farmer', required=True)
    cl_rice_byproduct_fee_policy = fields.Selection(
        [
            ('in_addition', "Kept in addition to the milling fee"),
            ('replace', "Kept instead of the milling fee"),
        ],
        string="Bran and Husk", default='in_addition', required=True,
        help="Bran and husk always go to the mill. They are either kept on top of the milling fee, "
             "or they pay for the milling and no fee is charged.")

    # Storage
    cl_rice_storage_enabled = fields.Boolean(string="Charge Storage Fees", default=True)
    cl_rice_storage_basis = fields.Selection(
        [('bag', "Per bag per month"), ('kg', "Per kg per month")],
        string="Storage Fee Basis", default='bag', required=True)
    cl_rice_storage_rate = fields.Monetary(string="Storage Fee Rate", currency_field='currency_id')
    cl_rice_storage_free_days = fields.Integer(string="Free Storage (days)", default=30)
    cl_rice_paddy_bag_kg = fields.Float(string="Standard Paddy Bag (kg)", default=90.0)
    cl_rice_rice_bag_kg = fields.Float(string="Standard Rice Bag (kg)", default=50.0)
    cl_rice_bag_tare = fields.Float(string="Tare per Bag (kg)", default=0.5, digits='Product Unit')

    # Stock: one warehouse, three partitions
    cl_rice_warehouse_id = fields.Many2one('stock.warehouse', string="Rice Mill Warehouse")
    cl_rice_paddy_location_id = fields.Many2one('stock.location', string="Paddy Partition")
    cl_rice_rice_location_id = fields.Many2one('stock.location', string="Milled Rice Partition")
    cl_rice_byproduct_location_id = fields.Many2one('stock.location', string="By-products Partition")
    cl_rice_mill_location_id = fields.Many2one('stock.location', string="Mill Location")
    cl_rice_released_location_id = fields.Many2one(
        'stock.location', string="Released to Buyers",
        help="Farmer stock released to the buyers the farmers sold to.")
    cl_rice_consume_type_id = fields.Many2one('stock.picking.type', string="Mill Consumption Operation")
    cl_rice_output_type_id = fields.Many2one('stock.picking.type', string="Mill Output Operation")
    cl_rice_release_type_id = fields.Many2one('stock.picking.type', string="Stock Release Operation")

    # Accounts
    cl_rice_loan_account_id = fields.Many2one('account.account', string="Farmer Loans Receivable")
    cl_rice_service_account_id = fields.Many2one('account.account', string="Farmer Service Receivable")
    cl_rice_funds_held_account_id = fields.Many2one(
        'account.account', string="Farmer Funds Held",
        help="Money received for farmers (buyers paying through the mill). It is owed to the farmer, never income.")
    cl_rice_tractor_income_account_id = fields.Many2one('account.account', string="Tractor Income")
    cl_rice_harvester_income_account_id = fields.Many2one('account.account', string="Harvester Income")
    cl_rice_storage_income_account_id = fields.Many2one('account.account', string="Storage Income")
    cl_rice_milling_income_account_id = fields.Many2one('account.account', string="Milling Income")
    cl_rice_byproduct_income_account_id = fields.Many2one('account.account', string="By-products Sales Income")
    cl_rice_other_income_account_id = fields.Many2one('account.account', string="Other Services Income")

    # Machine and field costs
    cl_rice_fuel_price = fields.Monetary(
        string="Fuel Price per Litre", currency_field='currency_id',
        help="Values the litres used by service jobs and trips. Fuel bills go to the Fuel Clearing account.")
    cl_rice_fuel_clearing_account_id = fields.Many2one(
        'account.account', string="Fuel Clearing",
        help="Diesel bills are booked here; jobs and trips move the fuel they use to the machine costs.")
    cl_rice_operator_accrual_account_id = fields.Many2one('account.account', string="Accrued Operator Wages")
    cl_rice_machine_cost_account_id = fields.Many2one('account.account', string="Machine Running Cost")
    cl_rice_operator_wage_account_id = fields.Many2one('account.account', string="Operator Wages")
    cl_rice_repair_account_id = fields.Many2one('account.account', string="Spare Parts and Repairs")
    cl_rice_bags_account_id = fields.Many2one('account.account', string="Bags and Twine")
    cl_rice_transport_account_id = fields.Many2one('account.account', string="Staff Field Transport")
    cl_rice_airtime_account_id = fields.Many2one('account.account', string="Airtime and Communication")
    cl_rice_office_account_id = fields.Many2one('account.account', string="Office and Warehouse Costs")
    cl_rice_mill_hours_per_day = fields.Float(
        string="Mill Working Hours per Day", default=10.0,
        help="Hours a mill can work per day (Monday to Saturday): the dashboard measures its utilisation against it.")
    cl_rice_dashboard_lot_age_days = fields.Integer(
        string="Old Lot Alert (days)", default=90,
        help="The dashboard warns about lots kept in store longer than this.")
    cl_rice_post_expenses = fields.Boolean(
        string="Post Company-Paid Expenses", default=True,
        help="Company-paid expenses are posted when approved, so they reach the accounts and the cash book.")

    # Journals
    cl_rice_cost_journal_id = fields.Many2one('account.journal', string="Machine and Field Costs Journal")
    cl_rice_charge_journal_id = fields.Many2one('account.journal', string="Farmer Charges Journal")
    cl_rice_walkin_journal_id = fields.Many2one('account.journal', string="Walk-in Milling Journal")
    cl_rice_loan_journal_id = fields.Many2one('account.journal', string="Farmer Accounts Journal")
    cl_rice_settlement_journal_id = fields.Many2one('account.journal', string="Settlement Journal")

    @api.model
    def _cl_rice_account_specs(self):
        return RICE_ACCOUNTS

    @api.model
    def _cl_rice_journal_specs(self):
        return RICE_JOURNALS

    def _cl_rice_setup_company(self):
        """Create the rice mill stock setup, accounts and journals the companies still miss.

        Safe to call several times: only empty configuration fields are filled.
        """
        for company in self:
            company._cl_rice_setup_stock()
            if not company.chart_template:
                # no chart of accounts yet: the setup wizard is run again later
                continue
            company._cl_rice_setup_accounting()

    def _cl_rice_setup_stock(self):
        self.ensure_one()
        category = self.env.ref('cl_rice_mill.product_category_rice_mill', raise_if_not_found=False)
        if category:
            # by-products kept by the mill are valued at their average cost
            category.with_company(self).sudo().property_cost_method = 'average'
        Location = self.env['stock.location'].sudo()
        vals = {}
        warehouse = self.cl_rice_warehouse_id
        if not warehouse:
            Warehouse = self.env['stock.warehouse'].sudo()
            warehouse = Warehouse.search([('company_id', '=', self.id), ('code', '=', 'RMW')], limit=1)
            if not warehouse:
                warehouse = Warehouse.create({'name': "Rice Mill Warehouse", 'code': 'RMW', 'company_id': self.id})
            vals['cl_rice_warehouse_id'] = warehouse.id
        for field_name, name in RICE_LOCATIONS:
            if not self[field_name]:
                vals[field_name] = Location.create({
                    'name': name,
                    'usage': 'internal',
                    'location_id': warehouse.lot_stock_id.id,
                    'company_id': self.id,
                }).id
        if not self.cl_rice_mill_location_id:
            vals['cl_rice_mill_location_id'] = Location.create({
                'name': "Mill",
                'usage': 'production',
                'company_id': self.id,
            }).id
        if not self.cl_rice_released_location_id:
            vals['cl_rice_released_location_id'] = Location.create({
                'name': "Released to Buyers",
                'usage': 'customer',
                'company_id': self.id,
            }).id
        if vals:
            self.sudo().write(vals)
        vals = {}
        PickingType = self.env['stock.picking.type'].sudo()
        for field_name, name, code, picking_code, source, dest in (
            ('cl_rice_consume_type_id', "Milling Consumption", 'MLC', 'internal',
             self.cl_rice_paddy_location_id, self.cl_rice_mill_location_id),
            ('cl_rice_output_type_id', "Milling Output", 'MLO', 'internal',
             self.cl_rice_mill_location_id, self.cl_rice_rice_location_id),
            ('cl_rice_release_type_id', "Stock Release", 'REL', 'outgoing',
             self.cl_rice_rice_location_id, self.cl_rice_released_location_id),
        ):
            if not self[field_name]:
                vals[field_name] = PickingType.create({
                    'name': name,
                    'code': picking_code,
                    'sequence_code': code,
                    'warehouse_id': warehouse.id,
                    'default_location_src_id': source.id,
                    'default_location_dest_id': dest.id,
                    'company_id': self.id,
                }).id
        if vals:
            self.sudo().write(vals)

    def _cl_rice_store_locations(self):
        """Internal locations holding the farmers' and the mill's stock."""
        self.ensure_one()
        return self.cl_rice_warehouse_id.lot_stock_id

    def _cl_rice_location_for(self, product):
        """Partition a rice mill product is stored in."""
        self.ensure_one()
        role = product.cl_rice_product
        if role == 'paddy':
            return self.cl_rice_paddy_location_id
        if role == 'rice':
            return self.cl_rice_rice_location_id
        return self.cl_rice_byproduct_location_id

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
        self._cl_rice_setup_expense_products()

    def _cl_rice_setup_documents(self):
        """Choose a document layout, so printing does not first open the layout configurator."""
        layout = self.env.ref('web.external_layout_standard', raise_if_not_found=False)
        for company in self.filtered(lambda c: not c.external_report_layout_id):
            if layout:
                company.sudo().external_report_layout_id = layout

    @api.model
    def _cl_rice_enable_analytic(self):
        """Turn Analytic Accounting on for every internal user, as the accounting setting does."""
        group = self.env.ref('analytic.group_analytic_accounting')
        employees = self.env.ref('base.group_user')
        if group not in employees.implied_ids:
            employees.sudo().write({'implied_ids': [(4, group.id)]})

    def _cl_rice_setup_expense_products(self):
        """Expense products (diesel, repairs, wages...) point at the rice mill cost accounts."""
        self.ensure_one()
        for xml_id, account_field in EXPENSE_PRODUCTS:
            product = self.env.ref(f'cl_rice_mill.{xml_id}', raise_if_not_found=False)
            template = product and product.product_tmpl_id.with_company(self).sudo()
            if template and self[account_field] and not template.property_account_expense_id:
                template.property_account_expense_id = self[account_field]

    def _cl_rice_setup_product_accounts(self):
        """Bran, husk and broken rice are sold by the mill to the by-products income account."""
        self.ensure_one()
        if not self.cl_rice_byproduct_income_account_id:
            return
        for xml_id in ('product_broken', 'product_bran', 'product_husk'):
            product = self.env.ref(f'cl_rice_mill.{xml_id}', raise_if_not_found=False)
            template = product and product.product_tmpl_id.with_company(self).sudo()
            if template and not template.property_account_income_id:
                template.property_account_income_id = self.cl_rice_byproduct_income_account_id
