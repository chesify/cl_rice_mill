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
]

# (field name, code, name, type)
RICE_JOURNALS = [
    ('cl_rice_charge_journal_id', 'RMCH', "Farmer Charges", 'sale'),
    ('cl_rice_walkin_journal_id', 'RMWI', "Walk-in Milling", 'sale'),
    ('cl_rice_loan_journal_id', 'RMLN', "Farmer Accounts", 'general'),
    ('cl_rice_settlement_journal_id', 'RMST', "Farmer Settlements", 'general'),
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

    # Journals
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
