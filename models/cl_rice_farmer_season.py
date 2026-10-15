from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .product_template import LOT_SUFFIX

LOCKED_STATES = ('settled', 'closed')
# fields that may still change on a locked farmer season
LOCK_FREE_FIELDS = {'state', 'note', 'active', 'activity_ids', 'message_follower_ids', 'message_ids'}


class ClRiceFarmerSeason(models.Model):
    _name = 'cl.rice.farmer.season'
    _description = "Farmer Season"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'season_id desc, name desc'

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    partner_id = fields.Many2one(
        'res.partner', string="Farmer (Mkulima)", required=True, index=True, tracking=True,
        domain="[('is_farmer', '=', True), ('cl_farmer_state', '=', 'approved')]")
    farmer_code = fields.Char(related='partner_id.cl_farmer_code', string="Farmer Code")
    season_id = fields.Many2one(
        'cl.rice.season', string="Season (Msimu)", required=True, index=True, tracking=True,
        domain="[('state', '!=', 'closed'), ('company_id', '=', company_id)]")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    field_officer_id = fields.Many2one(
        'res.users', string="Field Officer", tracking=True, index=True,
        compute='_compute_field_officer_id', store=True, readonly=False, precompute=True,
        domain="[('share', '=', False)]")
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('active', "Active"),
            ('harvested', "Harvested"),
            ('in_storage', "In Storage"),
            ('sold', "Sold"),
            ('settled', "Settled"),
            ('closed', "Closed"),
        ],
        default='draft', required=True, tracking=True, copy=False, index=True)
    is_locked = fields.Boolean(compute='_compute_is_locked')
    active = fields.Boolean(default=True)

    # Farm
    acreage = fields.Float(string="Acreage Planted", digits=(16, 2), tracking=True)
    plot_details = fields.Text(string="Plot Details")
    expected_yield = fields.Float(
        string="Expected Yield (kg/acre)", digits='Product Unit', tracking=True,
        compute='_compute_expected_yield', store=True, readonly=False, precompute=True)
    expected_production = fields.Float(
        string="Expected Production (kg)", digits='Product Unit', compute='_compute_credit_limit')
    reference_price = fields.Monetary(related='season_id.reference_price')

    # Credit
    credit_limit_computed = fields.Monetary(
        string="Computed Credit Limit", compute='_compute_credit_limit',
        help="Acreage x expected yield x reference price x loan-to-value.")
    credit_limit_override = fields.Monetary(string="Credit Limit Override", readonly=True, tracking=True, copy=False)
    credit_limit_override_reason = fields.Text(string="Override Reason", readonly=True, copy=False)
    credit_limit_override_uid = fields.Many2one('res.users', string="Overridden By", readonly=True, copy=False)
    credit_limit = fields.Monetary(string="Credit Limit", compute='_compute_credit_limit')

    analytic_account_id = fields.Many2one(
        'account.analytic.account', string="Analytic Account", readonly=True, copy=False, index='btree_not_null')
    task_id = fields.Many2one('project.task', string="Task", readonly=True, copy=False)
    note = fields.Html(string="Notes")

    # Documents
    loan_ids = fields.One2many('cl.rice.loan', 'farmer_season_id', string="Loans")
    service_job_ids = fields.One2many('cl.rice.service.job', 'farmer_season_id', string="Service Jobs")
    intake_ids = fields.One2many('cl.rice.intake', 'farmer_season_id', string="Intakes")
    milling_job_ids = fields.One2many('cl.rice.milling.job', 'farmer_season_id', string="Milling Jobs")
    storage_fee_ids = fields.One2many('cl.rice.storage.fee', 'farmer_season_id', string="Storage Fees")
    lot_ids = fields.One2many('stock.lot', 'cl_farmer_season_id', string="Lots")
    settlement_ids = fields.One2many('cl.rice.settlement', 'farmer_season_id', string="Settlements")
    proceeds_ids = fields.One2many('cl.rice.proceeds', 'farmer_season_id', string="Sale Proceeds")
    transfer_ids = fields.One2many('cl.rice.ownership.transfer', 'farmer_season_id', string="Crop Bought by the Mill")
    settlement_count = fields.Integer(compute='_compute_document_counts')
    loan_count = fields.Integer(compute='_compute_document_counts')
    service_job_count = fields.Integer(compute='_compute_document_counts')
    intake_count = fields.Integer(compute='_compute_document_counts')
    milling_count = fields.Integer(compute='_compute_document_counts')

    # Stock held (farmer-owned, in the stores)
    paddy_kg = fields.Float(string="Paddy Held (kg)", compute='_compute_stock_held', digits='Product Unit')
    paddy_bags = fields.Float(string="Paddy Bags", compute='_compute_stock_held', digits=(16, 1))
    rice_kg = fields.Float(string="Rice Held (kg)", compute='_compute_stock_held', digits='Product Unit')
    rice_bags = fields.Float(string="Rice Bags", compute='_compute_stock_held', digits=(16, 1))
    collateral_value = fields.Monetary(
        compute='_compute_stock_held', help="Paddy and rice held, valued at the season reference prices.")
    collateral_cover = fields.Float(
        string="Collateral Cover (%)", compute='_compute_stock_held', digits=(16, 1),
        help="Collateral value against the amount owed.")
    low_cover = fields.Boolean(string="Low Collateral Cover", compute='_compute_stock_held')

    # Live figures (what was charged, by kind)
    loan_total = fields.Monetary(string="Loans Disbursed", compute='_compute_charges')
    interest_total = fields.Monetary(string="Interest", compute='_compute_charges')
    service_total = fields.Monetary(string="Services", compute='_compute_charges')
    operator_total = fields.Monetary(string="Operators Paid", compute='_compute_charges')
    milling_total = fields.Monetary(string="Milling", compute='_compute_charges')
    storage_total = fields.Monetary(string="Storage", compute='_compute_charges')
    byproduct_credit_total = fields.Monetary(string="By-product Credits", compute='_compute_charges')
    charges_total = fields.Monetary(string="Total Charged", compute='_compute_charges')
    # Live balances (from accounting, after repayments)
    balance_owed = fields.Monetary(
        string="Owed by Farmer", compute='_compute_balances',
        help="Open balance of the farmer's loans and charges for this season.")
    credits_due = fields.Monetary(
        string="Due to Farmer", compute='_compute_balances',
        help="Open sale proceeds and credits the mill owes the farmer for this season.")
    net_balance = fields.Monetary(
        string="Net Balance", compute='_compute_balances',
        help="Due to the farmer minus owed by the farmer. Negative: the farmer still owes the mill.")
    exposure = fields.Monetary(compute='_compute_balances', help="Owed by the farmer plus loans waiting for disbursement.")
    over_limit = fields.Boolean(string="Over Credit Limit", compute='_compute_balances')

    _partner_season_uniq = models.Constraint(
        'UNIQUE(partner_id, season_id)', "A farmer can only have one farmer season per season.")
    _acreage_positive = models.Constraint('CHECK(acreage >= 0)', "The acreage cannot be negative.")
    _yield_positive = models.Constraint('CHECK(expected_yield >= 0)', "The expected yield cannot be negative.")
    _override_positive = models.Constraint(
        'CHECK(credit_limit_override >= 0)', "The credit limit override cannot be negative.")

    @api.depends('partner_id')
    def _compute_field_officer_id(self):
        for fs in self:
            fs.field_officer_id = fs.partner_id.cl_field_officer_id or fs.field_officer_id

    @api.depends('company_id')
    def _compute_expected_yield(self):
        for fs in self:
            if not fs.expected_yield:
                fs.expected_yield = fs.company_id.cl_rice_default_yield

    @api.depends('state')
    def _compute_is_locked(self):
        for fs in self:
            fs.is_locked = fs.state in LOCKED_STATES

    @api.depends(
        'acreage', 'expected_yield', 'season_id.reference_price',
        'company_id.cl_rice_ltv_percent', 'credit_limit_override')
    def _compute_credit_limit(self):
        for fs in self:
            fs.expected_production = fs.acreage * fs.expected_yield
            fs.credit_limit_computed = fs.currency_id.round(
                fs.expected_production * fs.season_id.reference_price * fs.company_id.cl_rice_ltv_percent / 100.0)
            fs.credit_limit = fs.credit_limit_override or fs.credit_limit_computed

    def _compute_document_counts(self):
        loans = dict(self.env['cl.rice.loan']._read_group(
            [('farmer_season_id', 'in', self.ids)], ['farmer_season_id'], ['__count']))
        jobs = dict(self.env['cl.rice.service.job']._read_group(
            [('farmer_season_id', 'in', self.ids)], ['farmer_season_id'], ['__count']))
        intakes = dict(self.env['cl.rice.intake']._read_group(
            [('farmer_season_id', 'in', self.ids), ('state', '=', 'done')], ['farmer_season_id'], ['__count']))
        millings = dict(self.env['cl.rice.milling.job']._read_group(
            [('farmer_season_id', 'in', self.ids)], ['farmer_season_id'], ['__count']))
        for fs in self:
            fs.loan_count = loans.get(fs, 0)
            fs.service_job_count = jobs.get(fs, 0)
            fs.intake_count = intakes.get(fs, 0)
            fs.milling_count = millings.get(fs, 0)
            fs.settlement_count = len(fs.settlement_ids)

    def _compute_stock_held(self):
        lots = self.env['stock.lot'].sudo().search([
            ('cl_farmer_season_id', 'in', self.ids),
            ('product_id.cl_rice_product', 'in', ('paddy', 'rice')),
        ])
        quants = self.env['stock.quant'].sudo().search([
            ('lot_id', 'in', lots.ids),
            ('location_id.usage', '=', 'internal'),
        ])
        kg = {}
        for quant in quants:
            fs = quant.lot_id.cl_farmer_season_id
            if quant.owner_id != fs.partner_id:
                continue  # sold or bought by the mill
            key = (fs.id, quant.product_id.cl_rice_product)
            kg[key] = kg.get(key, 0.0) + quant.quantity
        bags = {}
        for lot in lots:
            key = (lot.cl_farmer_season_id.id, lot.product_id.cl_rice_product)
            bags[key] = bags.get(key, 0.0) + lot.cl_bag_qty
        owed, _due = self._cl_open_balances()
        for fs in self:
            fs.paddy_kg = kg.get((fs.id, 'paddy'), 0.0)
            fs.rice_kg = kg.get((fs.id, 'rice'), 0.0)
            fs.paddy_bags = bags.get((fs.id, 'paddy'), 0.0) if fs.paddy_kg else 0.0
            fs.rice_bags = bags.get((fs.id, 'rice'), 0.0) if fs.rice_kg else 0.0
            fs.collateral_value = fs.currency_id.round(
                fs.paddy_kg * fs.season_id.reference_price + fs.rice_kg * fs.season_id.rice_reference_price)
            balance = owed.get(fs.id, 0.0)
            fs.collateral_cover = fs.collateral_value / balance * 100.0 if balance > 0 else 0.0
            fs.low_cover = balance > 0 and fs.collateral_cover < fs.company_id.cl_rice_min_cover_percent

    @api.depends('loan_ids.state', 'loan_ids.amount', 'loan_ids.interest_total',
                 'service_job_ids.state', 'service_job_ids.amount', 'service_job_ids.operator_paid_amount',
                 'milling_job_ids.state', 'milling_job_ids.fee_amount', 'milling_job_ids.byproduct_credit',
                 'storage_fee_ids.amount')
    def _compute_charges(self):
        for fs in self:
            loans = fs.loan_ids.filtered(lambda l: l.state in ('disbursed', 'settled'))
            jobs = fs.service_job_ids.filtered(lambda j: j.state == 'charged')
            fs.loan_total = sum(loans.mapped('amount'))
            fs.interest_total = sum(loans.mapped('interest_total'))
            fs.service_total = sum(jobs.mapped('amount'))
            fs.operator_total = sum(jobs.mapped('operator_paid_amount'))
            millings = fs.milling_job_ids.filtered(lambda j: j.state == 'done')
            fs.milling_total = sum(millings.mapped('fee_amount'))
            fs.byproduct_credit_total = sum(millings.mapped('byproduct_credit'))
            fs.storage_total = sum(fs.storage_fee_ids.mapped('amount'))
            fs.charges_total = fs._cl_charges_total()

    def _cl_charges_total(self):
        self.ensure_one()
        return (self.loan_total + self.interest_total + self.service_total + self.operator_total
                + self.milling_total + self.storage_total)

    @api.depends('loan_ids.state', 'loan_ids.interest_total', 'service_job_ids.state',
                 'milling_job_ids.state', 'storage_fee_ids')
    def _compute_balances(self):
        owed, due = self._cl_open_balances()
        pending = dict(self.env['cl.rice.loan']._read_group(
            [('farmer_season_id', 'in', self.ids), ('state', 'in', ('to_approve', 'approved'))],
            ['farmer_season_id'], ['amount:sum']))
        for fs in self:
            fs.balance_owed = owed.get(fs.id, 0.0)
            fs.credits_due = due.get(fs.id, 0.0)
            fs.net_balance = fs.credits_due - fs.balance_owed
            fs.exposure = fs.balance_owed + pending.get(fs, 0.0)
            fs.over_limit = fs.currency_id.compare_amounts(fs.exposure, fs.credit_limit) > 0

    def _cl_open_balances(self):
        """Open receivable (owed) and payable (due) balances per farmer season id."""
        owed, due = {}, {}
        if not self.ids:
            return owed, due
        groups = self.env['account.move.line'].sudo()._read_group(
            [
                ('cl_farmer_season_id', 'in', self.ids),
                ('parent_state', '=', 'posted'),
                ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
            ],
            ['cl_farmer_season_id', 'account_id'],
            ['amount_residual:sum'],
        )
        for fs, account, residual in groups:
            if account.account_type == 'asset_receivable':
                owed[fs.id] = owed.get(fs.id, 0.0) + residual
            else:
                due[fs.id] = due.get(fs.id, 0.0) - residual
        return owed, due

    def _cl_exposure(self, exclude_loans=None):
        """Owed balance plus the loans waiting for disbursement (except ``exclude_loans``)."""
        self.ensure_one()
        pending = self.loan_ids.filtered(lambda l: l.state in ('to_approve', 'approved'))
        if exclude_loans:
            pending -= exclude_loans
        # read the balance from the database: the posted entries may be newer than the cache
        owed, _due = self._cl_open_balances()
        return owed.get(self.id, 0.0) + sum(pending.mapped('amount'))

    @api.depends('partner_id', 'season_id')
    def _compute_display_name(self):
        for fs in self:
            fs.display_name = f"{fs.name} - {fs.partner_id.name or ''} ({fs.season_id.name or ''})"

    @api.constrains('partner_id')
    def _check_partner_is_farmer(self):
        for fs in self:
            if not fs.partner_id.is_farmer:
                raise ValidationError(self.env._("%s is not registered as a farmer.", fs.partner_id.name))

    @api.model_create_multi
    def create(self, vals_list):
        Sequence = self.env['ir.sequence']
        for vals in vals_list:
            if vals.get('name', self.env._("New")) == self.env._("New"):
                company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
                vals['name'] = Sequence.with_company(company).next_by_code('cl.rice.farmer.season') or '/'
        records = super().create(vals_list)
        records._cl_create_analytic_and_task()
        return records

    def write(self, vals):
        if not self.env.context.get('cl_rice_unlock') and set(vals) - LOCK_FREE_FIELDS:
            locked = self.filtered('is_locked')
            if locked:
                raise UserError(self.env._(
                    "Farmer season %s is settled and locked. Corrections must go through a reversal.",
                    locked[0].name))
        if 'partner_id' in vals or 'season_id' in vals:
            if self.filtered(lambda fs: fs.state != 'draft'):
                raise UserError(self.env._("The farmer and the season can only be changed on a draft farmer season."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda fs: fs.state != 'draft'):
            raise UserError(self.env._("Only draft farmer seasons can be deleted; archive the others."))

    def _cl_create_analytic_and_task(self):
        AnalyticAccount = self.env['account.analytic.account'].sudo()
        Task = self.env['project.task'].sudo()
        for fs in self:
            fs.analytic_account_id = AnalyticAccount.create({
                'name': f"{fs.partner_id.cl_farmer_code or ''} {fs.partner_id.name} - {fs.season_id.name}".strip(),
                'code': fs.name,
                'plan_id': fs.season_id.analytic_plan_id.id,
                'partner_id': fs.partner_id.id,
                'company_id': fs.company_id.id,
            })
            if fs.season_id.project_id:
                fs.task_id = Task.create({
                    'name': f"{fs.name} - {fs.partner_id.name}",
                    'project_id': fs.season_id.project_id.id,
                    'partner_id': fs.partner_id.id,
                    'user_ids': [(6, 0, fs.field_officer_id.ids)],
                })

    # ------------------------------------------------------------------
    # Accounting helpers
    # ------------------------------------------------------------------
    def _cl_analytic_distribution(self, extra_accounts=None):
        """Analytic distribution putting 100% on this farmer season and on each extra account
        (each extra account must belong to another plan)."""
        self.ensure_one()
        accounts = self.analytic_account_id | (extra_accounts or self.env['account.analytic.account'])
        return {",".join(str(account_id) for account_id in accounts.ids): 100.0}

    def _cl_tag_receivable_lines(self, moves):
        """Link the farmer's receivable and payable items of ``moves`` to this farmer season."""
        self.ensure_one()
        moves.line_ids.filtered(
            lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
            and l.partner_id.commercial_partner_id == self.partner_id.commercial_partner_id
        ).cl_farmer_season_id = self

    def _cl_find_lot(self, product):
        self.ensure_one()
        return self.env['stock.lot'].sudo().search([
            ('cl_farmer_season_id', '=', self.id),
            ('product_id', '=', product.id),
        ], limit=1)

    def _cl_get_lot(self, product):
        """The lot holding this farmer's ``product`` for the season, created on first use."""
        self.ensure_one()
        lot = self._cl_find_lot(product)
        if not lot:
            suffix = LOT_SUFFIX.get(product.cl_rice_product) or product.default_code or 'X'
            lot = self.env['stock.lot'].sudo().create({
                'name': f"{self.partner_id.cl_farmer_code or self.partner_id.id}-{self.season_id.code}-{suffix}",
                'product_id': product.id,
                'company_id': self.company_id.id,
                'cl_farmer_season_id': self.id,
                'cl_season_id': self.season_id.id,
                'cl_owner_id': self.partner_id.id,
            })
        return lot

    def action_open_settle(self):
        self.ensure_one()
        self._cl_check_open()
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only a Rice Mill Accountant can settle farmers."))
        return {
            'name': self.env._("Settle %s", self.partner_id.name),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.settlement.create',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_farmer_season_id': self.id},
        }

    def action_view_settlements(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_settlement_action')
        action['domain'] = [('farmer_season_id', '=', self.id)]
        return action

    def action_buy_crop(self):
        self.ensure_one()
        self._cl_check_open()
        return {
            'name': self.env._("Buy Crop from %s", self.partner_id.name),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.ownership.transfer',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_farmer_season_id': self.id},
        }

    def action_view_intakes(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_intake_action')
        action['domain'] = [('farmer_season_id', '=', self.id)]
        action['context'] = {'default_farmer_season_id': self.id}
        return action

    def action_view_milling_jobs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_milling_job_action')
        action['domain'] = [('farmer_season_id', '=', self.id)]
        action['context'] = {'default_farmer_season_id': self.id}
        return action

    def action_view_loans(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_loan_action')
        action['domain'] = [('farmer_season_id', '=', self.id)]
        action['context'] = {'default_farmer_season_id': self.id}
        return action

    def action_view_service_jobs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_service_job_action')
        action['domain'] = [('farmer_season_id', '=', self.id)]
        action['context'] = {'default_farmer_season_id': self.id}
        return action

    def action_view_journal_items(self):
        self.ensure_one()
        return {
            'name': self.env._("Farmer Account"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move.line',
            'view_mode': 'list',
            'domain': [('cl_farmer_season_id', '=', self.id), ('parent_state', '=', 'posted')],
        }

    # ------------------------------------------------------------------
    # Lock helpers used by every document that charges a farmer season
    # ------------------------------------------------------------------
    def _cl_check_open(self):
        locked = self.filtered('is_locked')
        if locked:
            raise UserError(self.env._(
                "Farmer season %s is settled: no new charges can be added. Use a reversal to correct it.",
                locked[0].name))

    def _cl_advance_state(self, from_states, to_state):
        """Move farmer seasons forward when an operation happens (intake, sale, settlement).

        Done as superuser: the operation is allowed to the user (a warehouse officer receiving
        paddy) even though editing the farmer season itself is not; only the state changes.
        """
        self.filtered(lambda fs: fs.state in from_states).sudo().with_context(cl_rice_unlock=True).state = to_state

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_activate(self):
        for fs in self:
            if fs.state != 'draft':
                raise UserError(self.env._("Only draft farmer seasons can be activated."))
            if fs.partner_id.cl_farmer_state != 'approved':
                raise UserError(self.env._("The farmer %s is not approved.", fs.partner_id.name))
        self.state = 'active'

    def action_mark_harvested(self):
        if self.filtered(lambda fs: fs.state != 'active'):
            raise UserError(self.env._("Only active farmer seasons can be marked as harvested."))
        self.state = 'harvested'

    def action_close(self):
        if self.filtered(lambda fs: fs.state != 'settled'):
            raise UserError(self.env._("Only settled farmer seasons can be closed."))
        self.state = 'closed'

    def action_open_credit_override(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Override Credit Limit"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_action': 'credit_override',
                'default_amount': self.credit_limit,
            },
        }

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_reason_actions(self):
        """Actions the reason wizard may run on this model: name -> callable(reason, amount)."""
        return {'credit_override': self._cl_apply_credit_override}

    def _cl_apply_credit_override(self, reason, amount):
        self.ensure_one()
        self._cl_check_manager()
        self._cl_check_open()
        if amount < 0:
            raise UserError(self.env._("The credit limit cannot be negative."))
        old_limit = self.credit_limit
        self.write({
            'credit_limit_override': amount,
            'credit_limit_override_reason': reason,
            'credit_limit_override_uid': self.env.uid,
        })
        self.message_post(body=self.env._(
            "Credit limit overridden by %(user)s from %(old)s to %(new)s. Reason: %(reason)s",
            user=self.env.user.name,
            old=self.currency_id.format(old_limit),
            new=self.currency_id.format(amount),
            reason=reason))
