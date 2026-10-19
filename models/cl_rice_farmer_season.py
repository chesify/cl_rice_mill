from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .product_template import LOT_SUFFIX
from .project_task_type import STATE_STAGES

LOCKED_STATES = ('settled', 'closed')
# fields that may still change on a locked farmer season
LOCK_FREE_FIELDS = {'state', 'note', 'active', 'activity_ids', 'message_follower_ids', 'message_ids'}


class ClRiceFarmerSeason(models.Model):
    _name = 'cl.rice.farmer.season'
    _description = "Farmer Season"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'season_id desc, name desc'
    _rec_names_search = ['name', 'partner_id.name', 'partner_id.cl_farmer_code']

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    partner_id = fields.Many2one(
        'res.partner', string="Farmer", required=True, index=True, tracking=True,
        domain="[('is_farmer', '=', True), ('cl_farmer_state', '=', 'approved')]")
    farmer_code = fields.Char(related='partner_id.cl_farmer_code', string="Farmer Code")
    season_id = fields.Many2one(
        'cl.rice.season', string="Season", required=True, index=True, tracking=True,
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
            ('released', "Stock Released"),
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
    facilitated_sale_ids = fields.One2many('cl.rice.facilitated.sale', 'farmer_season_id', string="Facilitated Sales")
    payment_ids = fields.One2many('cl.rice.farmer.payment', 'farmer_season_id', string="Payments Received")
    lot_ids = fields.One2many('stock.lot', 'cl_farmer_season_id', string="Lots")
    settlement_ids = fields.One2many('cl.rice.settlement', 'farmer_season_id', string="Settlements")
    settlement_count = fields.Integer(compute='_compute_document_counts')
    loan_count = fields.Integer(compute='_compute_document_counts')
    service_job_count = fields.Integer(compute='_compute_document_counts')
    intake_count = fields.Integer(compute='_compute_document_counts')
    milling_count = fields.Integer(compute='_compute_document_counts')
    sale_count = fields.Integer(compute='_compute_document_counts')

    # Stock held (farmer-owned, in the warehouse)
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
    stock_held_kg = fields.Float(
        string="Stock Held (kg)", compute='_compute_stock_held', digits='Product Unit',
        help="Everything the farmer still owns in the warehouse (paddy, rice, broken rice).")
    override_outstanding = fields.Boolean(
        string="Override Outstanding", compute='_compute_override_outstanding',
        search='_search_override_outstanding',
        help="Stock was released on a manager override and the farmer has not paid yet.")
    override_promised_date = fields.Date(
        string="Promised Payment", compute='_compute_override_outstanding')

    # Live figures (what was charged, by kind)
    loan_total = fields.Monetary(string="Loans Given", compute='_compute_charges')
    service_total = fields.Monetary(string="Services", compute='_compute_charges')
    operator_total = fields.Monetary(string="Operators Paid", compute='_compute_charges')
    milling_total = fields.Monetary(string="Milling", compute='_compute_charges')
    storage_total = fields.Monetary(string="Storage", compute='_compute_charges')
    opening_total = fields.Monetary(string="Opening Balance", compute='_compute_charges')
    charges_total = fields.Monetary(string="Total Charged", compute='_compute_charges')
    paid_total = fields.Monetary(string="Paid by the Farmer", compute='_compute_charges')
    # Live balances (from accounting)
    balance_owed = fields.Monetary(
        string="Owed by Farmer", compute='_compute_balances',
        help="Open balance of the farmer's loans and charges for this season.")
    funds_held = fields.Monetary(
        string="Funds Held for Farmer", compute='_compute_balances',
        help="Money the mill holds for the farmer (buyer payments received, payouts not yet made).")
    net_balance = fields.Monetary(
        string="Net Balance", compute='_compute_balances',
        help="Funds held minus owed. Negative: the farmer still owes the mill.")
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
        def count(model, domain=()):
            return dict(self.env[model]._read_group(
                [('farmer_season_id', 'in', self.ids), *domain], ['farmer_season_id'], ['__count']))

        loans = count('cl.rice.loan')
        jobs = count('cl.rice.service.job')
        intakes = count('cl.rice.intake', [('state', '=', 'done')])
        millings = count('cl.rice.milling.job')
        sales = count('cl.rice.facilitated.sale')
        for fs in self:
            fs.loan_count = loans.get(fs, 0)
            fs.service_job_count = jobs.get(fs, 0)
            fs.intake_count = intakes.get(fs, 0)
            fs.milling_count = millings.get(fs, 0)
            fs.sale_count = sales.get(fs, 0)
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
                continue
            key = (fs.id, quant.product_id.cl_rice_product)
            kg[key] = kg.get(key, 0.0) + quant.quantity
        bags = {}
        for lot in lots:
            key = (lot.cl_farmer_season_id.id, lot.product_id.cl_rice_product)
            bags[key] = bags.get(key, 0.0) + lot.cl_bag_qty
        owed, _due = self._cl_open_balances()
        held = self._cl_stock_held_by_season()
        for fs in self:
            fs.stock_held_kg = held.get(fs.id, 0.0)
            fs.paddy_kg = kg.get((fs.id, 'paddy'), 0.0)
            fs.rice_kg = kg.get((fs.id, 'rice'), 0.0)
            fs.paddy_bags = bags.get((fs.id, 'paddy'), 0.0) if fs.paddy_kg else 0.0
            fs.rice_bags = bags.get((fs.id, 'rice'), 0.0) if fs.rice_kg else 0.0
            fs.collateral_value = fs.currency_id.round(
                fs.paddy_kg * fs.season_id.reference_price + fs.rice_kg * fs.season_id.rice_reference_price)
            balance = owed.get(fs.id, 0.0)
            fs.collateral_cover = fs.collateral_value / balance * 100.0 if balance > 0 else 0.0
            fs.low_cover = balance > 0 and fs.collateral_cover < fs.company_id.cl_rice_min_cover_percent

    def _cl_stock_held_by_season(self):
        """Kg of every rice mill product the farmer still owns in the warehouse, per farmer season id."""
        quants = self.env['stock.quant'].sudo().search([
            ('lot_id.cl_farmer_season_id', 'in', self.ids),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ])
        held = {}
        for quant in quants:
            fs = quant.lot_id.cl_farmer_season_id
            if quant.owner_id == fs.partner_id:
                held[fs.id] = held.get(fs.id, 0.0) + quant.quantity
        return held

    def _cl_stock_held_kg(self):
        self.ensure_one()
        return self._cl_stock_held_by_season().get(self.id, 0.0)

    def _compute_override_outstanding(self):
        releases = self.env['cl.rice.stock.release'].sudo().search([
            ('farmer_season_id', 'in', self.ids),
            ('clearance_basis', '=', 'override'),
            ('state', 'in', ('cleared', 'released')),
        ], order='override_promised_date')
        owed, _due = self._cl_open_balances()
        for fs in self:
            fs_releases = releases.filtered(lambda r: r.farmer_season_id == fs)
            outstanding = bool(fs_releases) and fs.currency_id.compare_amounts(owed.get(fs.id, 0.0), 0) > 0
            fs.override_outstanding = outstanding
            fs.override_promised_date = outstanding and fs_releases[:1].override_promised_date

    def _search_override_outstanding(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return NotImplemented
        releases = self.env['cl.rice.stock.release'].sudo().search([
            ('clearance_basis', '=', 'override'), ('state', 'in', ('cleared', 'released'))])
        candidates = releases.farmer_season_id
        owed, _due = candidates._cl_open_balances()
        ids = [fs.id for fs in candidates if fs.currency_id.compare_amounts(owed.get(fs.id, 0.0), 0) > 0]
        positive = (operator == '=') == value
        return [('id', 'in' if positive else 'not in', ids)]

    def _cl_label(self, record, part=None):
        """How a loan, a charge or a payment reads on the farmer's statements, in plain words."""
        model = record._name
        if model == 'cl.rice.loan':
            if record.loan_type == 'carry_forward':
                return self.env._("Balance carried from the previous season (%s)", record.name)
            types = dict(record._fields['loan_type']._description_selection(self.env))
            return self.env._("Loan %(ref)s - %(type)s", ref=record.name, type=types[record.loan_type].lower())
        if model == 'cl.rice.service.job':
            if part == 'operator':
                return self.env._("Operators paid for you (%s)", record.name)
            types = dict(record._fields['service_type']._description_selection(self.env))
            units = dict(record._fields['uom']._description_selection(self.env))
            unit = units.get(record.uom, '').lower()
            return self.env._(
                "%(service)s, %(qty)s %(uom)s (%(ref)s)", service=types[record.service_type],
                qty=f"{record.quantity:g}", uom=unit + ('s' if unit and record.quantity != 1 else ''), ref=record.name)
        if model == 'cl.rice.milling.job':
            return self.env._("Milling %(kg)s kg of paddy (%(ref)s)", kg=f"{record.input_kg:,.0f}", ref=record.name)
        if model == 'cl.rice.storage.fee':
            basis = dict(record._fields['basis']._description_selection(self.env))[record.basis].lower()
            return self.env._(
                "Storage for %(period)s, %(qty)s %(basis)s of %(lot)s",
                period=record.period, qty=f"{record.quantity:g}", basis=basis, lot=record.lot_id.name)
        if model == 'cl.rice.farmer.payment':
            if record.payment_type == 'repayment':
                return self.env._("Payment received from you (%s)", record.name)
            return self.env._("Paid by %(buyer)s for your crop (%(ref)s)", buyer=record.payer_id.name, ref=record.name)
        if model == 'cl.rice.opening.balance.line':
            return self.env._("Opening balance brought forward (%s)", record.batch_id.name)
        return record.display_name

    def _cl_statement_breakdown(self):
        """Everything charged and received this season, in plain words, for statements.

        :return: list of dicts with ``date``, ``kind`` ('owed' or 'earned'), ``category``, ``name``, ``amount``
        """
        self.ensure_one()
        lines = []

        def add(date, kind, category, name, amount):
            if amount and not self.currency_id.is_zero(amount):
                lines.append({'date': date, 'kind': kind, 'category': category, 'name': name, 'amount': amount})

        for line in self.env['cl.rice.opening.balance.line'].search(
                [('farmer_season_id', '=', self.id), ('batch_id.state', '=', 'posted')]):
            add(line.batch_id.date, 'owed', 'opening',
                self.env._("Opening balance brought forward (%s)", line.batch_id.name), line.debt_total)
        for loan in self.loan_ids.filtered(lambda l: l.state in ('disbursed', 'settled')):
            add(loan.disbursement_date or loan.date, 'owed', 'loan', self._cl_label(loan), loan.amount)
        for job in self.service_job_ids.filtered(lambda j: j.state == 'charged'):
            add(job.date, 'owed', 'service', self._cl_label(job), job.amount)
            add(job.date, 'owed', 'operator', self._cl_label(job, 'operator'), job.operator_paid_amount)
        for job in self.milling_job_ids.filtered(lambda j: j.state == 'done'):
            add(job.date_end and job.date_end.date(), 'owed', 'milling', self._cl_label(job), job.fee_amount)
        for fee in self.storage_fee_ids:
            add(fee.date, 'owed', 'storage', self._cl_label(fee), fee.amount)
        for payment in self.payment_ids.filtered(lambda p: p.state == 'posted'):
            category = 'repayment' if payment.payment_type == 'repayment' else 'buyer_payment'
            add(payment.date, 'earned', category, self._cl_label(payment), payment.amount)
        for settlement in self.settlement_ids.filtered(
                lambda s: s.settlement_type == 'interim' and s.state == 'paid' and s.payment_id):
            add(settlement.payment_id.date, 'owed', 'payout', self.env._(
                "Paid to you (%s)", settlement.name), settlement.payment_id.amount)
        lines.sort(key=lambda line: (line['date'] or fields.Date.today(), line['kind']))
        for sequence, line in enumerate(lines):
            line['sequence'] = sequence
        return lines

    # ------------------------------------------------------------------
    # Farmer portal
    # ------------------------------------------------------------------
    def _cl_portal_stock(self):
        """Lots the farmer still holds in the warehouse: product, lot, kg and bags."""
        self.ensure_one()
        lots = self.env['stock.lot'].sudo().search([('cl_farmer_season_id', '=', self.id)])
        return [
            {'product': lot.product_id.name, 'lot': lot.name, 'kg': lot.cl_kg_held, 'bags': lot.cl_bag_qty,
             'since': lot.cl_first_in_date}
            for lot in lots if lot.cl_kg_held > 0
        ]

    def _cl_portal_timeline(self):
        """What happened on the farmer's season, newest first, in plain words."""
        self.ensure_one()
        fs = self.sudo()
        events = []

        def add(date, icon, title, detail='', amount=0.0, document=None):
            events.append({'date': date, 'icon': icon, 'title': title, 'detail': detail, 'amount': amount,
                           'document': document})

        for loan in fs.loan_ids.filtered(lambda l: l.state in ('disbursed', 'settled')):
            add(loan.disbursement_date or loan.date, 'fa-money', fs._cl_label(loan), loan.purpose or '', loan.amount,
                ('loan', loan.id))
        for job in fs.service_job_ids.filtered(lambda j: j.state == 'charged'):
            add(job.date, 'fa-truck', fs._cl_label(job), job.vehicle_id.display_name or '', job.amount)
        for intake in fs.intake_ids.filtered(lambda i: i.state == 'done'):
            add(intake.date.date(), 'fa-download', self.env._("Paddy received: %(kg)s kg in %(bags)s bags",
                kg=f"{intake.net_weight:,.0f}", bags=intake.bags), intake.name, 0.0, ('receipt', intake.id))
        for job in fs.milling_job_ids.filtered(lambda j: j.state == 'done'):
            add(job.date_end and job.date_end.date(), 'fa-cogs', self.env._(
                "Milled %(kg)s kg of paddy: %(rice)s kg of rice (%(pct)s%%)",
                kg=f"{job.input_kg:,.0f}", rice=f"{job.rice_kg:,.0f}", pct=round(job.recovery_pct, 1)),
                job.name, job.fee_amount, ('milling', job.id))
        releases = self.env['cl.rice.stock.release'].sudo().search(
            [('farmer_season_id', '=', fs.id), ('state', '=', 'released')])
        for release in releases:
            add(release.released_date and release.released_date.date(), 'fa-upload', self.env._(
                "Released %(kg)s kg to %(buyer)s", kg=f"{release.quantity:,.0f}", buyer=release.buyer_id.name),
                release.name)
        for payment in fs.payment_ids.filtered(lambda p: p.state == 'posted'):
            add(payment.date, 'fa-check-circle', fs._cl_label(payment), '', payment.amount)
        for settlement in fs.settlement_ids.filtered(lambda s: s.state not in ('draft', 'cancelled', 'reversed')):
            add(settlement.date, 'fa-flag-checkered', self.env._("Settlement %s", settlement.name),
                dict(settlement._fields['settlement_type']._description_selection(self.env))[settlement.settlement_type],
                settlement.net_amount, ('settlement', settlement.id))
        events.sort(key=lambda e: e['date'] or fields.Date.today(), reverse=True)
        return events

    def _cl_portal_request(self, request_type, message):
        """A farmer asks for a service or a loan from the portal: the field officer gets a to-do."""
        self.ensure_one()
        fs = self.sudo()
        labels = {'service': self.env._("Service request"), 'loan': self.env._("Loan request")}
        user = fs.field_officer_id or self.env.ref('cl_rice_mill.cl_rice_mill_group_manager').all_user_ids[:1]
        summary = f"{labels[request_type]} - {fs.partner_id.name}"
        fs.activity_schedule('mail.mail_activity_data_todo', user_id=user.id or self.env.uid,
                             summary=summary, note=message)
        fs.message_post(body=self.env._("%(summary)s from the farmer portal: %(message)s",
                                        summary=summary, message=message))
        return user

    def _cl_statement(self, date_from=None, date_to=None):
        """Farmer account statement: every charge and receipt between the dates with a running balance.

        A positive balance is owed by the farmer, a negative one is due to the farmer (funds held).
        """
        self.ensure_one()
        opening = 0.0
        lines = []
        for line in self._cl_statement_breakdown():
            date = line['date']
            signed = line['amount'] if line['kind'] == 'owed' else -line['amount']
            if date_from and date and date < date_from:
                opening += signed
                continue
            if date_to and date and date > date_to:
                continue
            lines.append(line)
        balance = opening
        for line in lines:
            balance += line['amount'] if line['kind'] == 'owed' else -line['amount']
            line['balance'] = balance
        charges = sum(line['amount'] for line in lines if line['kind'] == 'owed')
        receipts = sum(line['amount'] for line in lines if line['kind'] == 'earned')
        # interim payouts: how the money held was split between the balance and the farmer
        payouts = []
        for settlement in self.settlement_ids.filtered(
                lambda s: s.settlement_type == 'interim' and s.state == 'paid').sorted('date'):
            if (date_from and settlement.date < date_from) or (date_to and settlement.date > date_to):
                continue
            payouts.append({
                'date': settlement.date,
                'name': settlement.name,
                'applied': settlement.applied_amount,
                'paid': settlement.payment_id.amount if settlement.payment_id else 0.0,
            })
        return {
            'date_from': date_from,
            'date_to': date_to,
            'opening': opening,
            'lines': lines,
            'charges': charges,
            'receipts': receipts,
            'closing': balance,
            'payouts': payouts,
        }

    @api.depends('loan_ids.state', 'loan_ids.amount',
                 'service_job_ids.state', 'service_job_ids.amount', 'service_job_ids.operator_paid_amount',
                 'milling_job_ids.state', 'milling_job_ids.fee_amount', 'storage_fee_ids.amount',
                 'payment_ids.state', 'payment_ids.amount')
    def _compute_charges(self):
        opening = dict(self.env['cl.rice.opening.balance.line'].sudo()._read_group(
            [('farmer_season_id', 'in', self.ids), ('batch_id.state', '=', 'posted')],
            ['farmer_season_id'], ['debt_total:sum']))
        for fs in self:
            loans = fs.loan_ids.filtered(lambda l: l.state in ('disbursed', 'settled') and l.loan_type != 'carry_forward')
            jobs = fs.service_job_ids.filtered(lambda j: j.state == 'charged')
            fs.loan_total = sum(loans.mapped('amount')) + sum(
                fs.loan_ids.filtered(lambda l: l.state in ('disbursed', 'settled') and l.loan_type == 'carry_forward')
                .mapped('amount'))
            fs.service_total = sum(jobs.mapped('amount'))
            fs.operator_total = sum(jobs.mapped('operator_paid_amount'))
            fs.milling_total = sum(fs.milling_job_ids.filtered(lambda j: j.state == 'done').mapped('fee_amount'))
            fs.storage_total = sum(fs.storage_fee_ids.mapped('amount'))
            fs.opening_total = opening.get(fs, 0.0)
            fs.charges_total = (fs.loan_total + fs.service_total + fs.operator_total
                                + fs.milling_total + fs.storage_total + fs.opening_total)
            fs.paid_total = sum(fs.payment_ids.filtered(
                lambda p: p.state == 'posted' and p.payment_type == 'repayment').mapped('amount'))

    @api.depends('loan_ids.state', 'service_job_ids.state', 'milling_job_ids.state', 'storage_fee_ids',
                 'payment_ids.state', 'facilitated_sale_ids.state', 'settlement_ids.state')
    def _compute_balances(self):
        owed, due = self._cl_open_balances()
        pending = dict(self.env['cl.rice.loan']._read_group(
            [('farmer_season_id', 'in', self.ids), ('state', 'in', ('to_approve', 'approved'))],
            ['farmer_season_id'], ['amount:sum']))
        for fs in self:
            fs.balance_owed = owed.get(fs.id, 0.0)
            fs.funds_held = due.get(fs.id, 0.0)
            fs.net_balance = fs.funds_held - fs.balance_owed
            fs.exposure = fs.balance_owed + pending.get(fs, 0.0)
            fs.over_limit = fs.currency_id.compare_amounts(fs.exposure, fs.credit_limit) > 0

    def _cl_open_balances(self):
        """Open receivable (owed) and payable (due to the farmer) balances per farmer season id.

        Always read from the database: the entries posted in the same transaction may be newer than the cache.
        """
        owed, due = {}, {}
        if not self.ids:
            return owed, due
        self.env['account.move.line'].flush_model()
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

    def _cl_balance_owed(self):
        self.ensure_one()
        owed, _due = self._cl_open_balances()
        return owed.get(self.id, 0.0)

    def _cl_exposure(self, exclude_loans=None):
        """Owed balance plus the loans waiting for disbursement (except ``exclude_loans``)."""
        self.ensure_one()
        pending = self.loan_ids.filtered(lambda l: l.state in ('to_approve', 'approved'))
        if exclude_loans:
            pending -= exclude_loans
        return self._cl_balance_owed() + sum(pending.mapped('amount'))

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
        res = super().write(vals)
        if 'state' in vals:
            self._cl_move_task(STATE_STAGES.get(vals['state']))
        return res

    def _cl_move_task(self, key):
        """Move the farmer's task of the season project forward to the stage ``key``.

        Tasks never move back: a stage set by hand further on (Planting) is kept.
        """
        if not key:
            return
        for fs in self.filtered('task_id'):
            stage = fs.season_id._cl_task_stage(key)
            task = fs.task_id.sudo()
            current = task.stage_id
            if stage and stage != current and (
                    not current.cl_rice_stage or current.sequence < stage.sequence):
                task.stage_id = stage

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda fs: fs.state != 'draft'):
            raise UserError(self.env._("Only draft farmer seasons can be deleted; archive the others."))

    def _cl_analytic_name(self):
        """The season is the analytic plan already: the account only names the farmer."""
        self.ensure_one()
        return f"{self.partner_id.cl_farmer_code or ''} {self.partner_id.name}".strip()

    def _cl_create_analytic_and_task(self):
        AnalyticAccount = self.env['account.analytic.account'].sudo()
        Task = self.env['project.task'].sudo()
        for fs in self:
            fs.analytic_account_id = AnalyticAccount.create({
                'name': fs._cl_analytic_name(),
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
                    'stage_id': fs.season_id._cl_task_stage('registered').id,
                })

    # ------------------------------------------------------------------
    # Accounting helpers
    # ------------------------------------------------------------------
    def _cl_analytic_distribution(self, extra_accounts=None):
        """Analytic distribution putting 100% on this farmer season and on each extra account
        (each extra account must belong to another plan)."""
        self.ensure_one()
        accounts = (self.analytic_account_id | self.season_id.project_id.account_id
                    | (extra_accounts or self.env['account.analytic.account']))
        return {",".join(str(account_id) for account_id in accounts.ids): 100.0}

    def _cl_tag_receivable_lines(self, moves):
        """Link the farmer's receivable and payable items of ``moves`` to this farmer season."""
        self.ensure_one()
        moves.line_ids.filtered(
            lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
            and l.partner_id.commercial_partner_id == self.partner_id.commercial_partner_id
        ).cl_farmer_season_id = self

    def _cl_open_receivables(self):
        self.ensure_one()
        return self.env['account.move.line'].sudo().search([
            ('cl_farmer_season_id', '=', self.id),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('reconciled', '=', False),
            ('amount_residual', '>', 0),
        ], order='date, id')

    def _cl_post_allocation(self, journal, debit_vals, amount, ref, date=None, excess_to_funds=False):
        """Post one entry paying the farmer's open receivables, oldest first.

        :param debit_vals: values of the debit line (where the money comes from)
        :param amount: amount available to pay with
        :param excess_to_funds: credit what is left over to the farmer's funds held,
            otherwise only the needed amount is debited
        :return: (move, amount applied to receivables)
        """
        self.ensure_one()
        company = self.company_id
        currency = self.currency_id
        remaining = amount
        credit_vals, targets = [], []
        for item in self._cl_open_receivables():
            if currency.compare_amounts(remaining, 0) <= 0:
                break
            take = min(item.amount_residual, remaining)
            credit_vals.append({
                'name': ref,
                'account_id': item.account_id.id,
                'partner_id': item.partner_id.id,
                'credit': take,
                'cl_farmer_season_id': self.id,
            })
            targets.append(item)
            remaining -= take
        applied = amount - remaining
        if excess_to_funds and currency.compare_amounts(remaining, 0) > 0:
            if not company.cl_rice_funds_held_account_id:
                raise UserError(self.env._("Set the farmer funds held account in the Rice Mill settings."))
            credit_vals.append({
                'name': self.env._("Overpayment held for the farmer - %s", ref),
                'account_id': company.cl_rice_funds_held_account_id.id,
                'partner_id': self.partner_id.id,
                'credit': remaining,
                'cl_farmer_season_id': self.id,
            })
        debit_amount = amount if excess_to_funds else applied
        if currency.is_zero(debit_amount):
            return self.env['account.move'], 0.0
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': date or fields.Date.context_today(self),
            'ref': ref,
            'cl_farmer_season_id': self.id,
            'company_id': company.id,
            'line_ids': [(0, 0, dict(debit_vals, name=debit_vals.get('name') or ref, debit=debit_amount))]
            + [(0, 0, vals) for vals in credit_vals],
        })
        move.action_post()
        new_credits = move.line_ids.sorted('id')[1:len(targets) + 1]
        for item, counterpart in zip(targets, new_credits):
            (item | counterpart).reconcile()
        return move, applied

    def _cl_funds_held_lines(self):
        """Open items of the farmer's funds held account, oldest first."""
        self.ensure_one()
        return self.env['account.move.line'].sudo().search([
            ('cl_farmer_season_id', '=', self.id),
            ('parent_state', '=', 'posted'),
            ('account_id', '=', self.company_id.cl_rice_funds_held_account_id.id),
            ('reconciled', '=', False),
            ('amount_residual', '<', 0),
        ], order='date, id')

    def _cl_funds_held_available(self):
        self.ensure_one()
        return -sum(self._cl_funds_held_lines().mapped('amount_residual'))

    def _cl_payout_split(self, funds_held, owed, payout):
        """How an interim payout splits the money held.

        :return: dict with ``applied_amount`` (to the debt), ``kept_amount`` (stays held), ``owed_after``
            and ``needs_debt_approval`` (paid out while the farmer still owes)
        """
        currency = self.currency_id or self.env.company.currency_id
        funds_held, owed, payout = funds_held or 0.0, owed or 0.0, max(payout or 0.0, 0.0)
        applied = currency.round(max(min(funds_held - payout, owed), 0.0))
        owed_after = currency.round(max(owed - applied, 0.0))
        return {
            'applied_amount': applied,
            'kept_amount': currency.round(max(funds_held - payout - applied, 0.0)),
            'owed_after': owed_after,
            'needs_debt_approval': bool(currency.compare_amounts(payout, 0) > 0
                                        and currency.compare_amounts(owed_after, 0) > 0),
        }

    def _cl_cover_for(self, owed):
        """Collateral cover (%) of the stock held against ``owed``; 0 when nothing is owed."""
        if len(self) != 1 or not owed or owed <= 0:
            return 0.0
        return self.collateral_value / owed * 100.0

    def _cl_apply_funds_held(self, limit=None):
        """Pay the farmer's open balance with the money the mill holds for him.

        :param limit: apply at most this amount
        :return: (amount applied, journal entry)
        """
        self.ensure_one()
        company = self.company_id
        funds_account = company.cl_rice_funds_held_account_id
        funds_lines = self._cl_funds_held_lines()
        amount = min(-sum(funds_lines.mapped('amount_residual')), self._cl_balance_owed())
        if limit is not None:
            amount = min(amount, limit)
        if self.currency_id.compare_amounts(amount, 0) <= 0:
            return 0.0, self.env['account.move']
        move, applied = self._cl_post_allocation(
            company.cl_rice_settlement_journal_id,
            {'account_id': funds_account.id, 'partner_id': self.partner_id.id, 'cl_farmer_season_id': self.id},
            amount,
            self.env._("Funds held applied to the balance of %s", self.name),
        )
        if move:
            (move.line_ids.filtered(lambda l: l.account_id == funds_account) | funds_lines).reconcile()
        return applied, move

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

    # ------------------------------------------------------------------
    # Business buttons
    # ------------------------------------------------------------------
    def _cl_open_form(self, model, name, context=None):
        self.ensure_one()
        return {
            'name': name,
            'type': 'ir.actions.act_window',
            'res_model': model,
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_farmer_season_id': self.id, **(context or {})},
        }

    def action_give_loan(self):
        self._cl_check_open()
        return self._cl_open_form('cl.rice.loan', self.env._("Give Loan"))

    def action_record_service(self):
        self._cl_check_open()
        return self._cl_open_form('cl.rice.service.job', self.env._("Record Service"))

    def action_receive_payment(self):
        self._cl_check_open()
        return self._cl_open_form('cl.rice.farmer.payment', self.env._("Receive Payment"))

    def action_release_stock(self):
        self._cl_check_open_for_release()
        return self._cl_open_form('cl.rice.facilitated.sale', self.env._("Release Stock"))

    def action_send_balance(self):
        """Send the farmer his balance (WhatsApp / SMS, when a messaging bridge is installed)."""
        self.ensure_one()
        sent = self.partner_id._cl_rice_notify('balance', self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': self.env._("Balance sent to %s.", self.partner_id.name) if sent
                else self.env._("No messaging channel is set up: install the WhatsApp / SMS bridge."),
                'type': 'success' if sent else 'warning',
            },
        }

    def action_open_settle(self):
        """Pay Farmer: settle the season and pay out what is due."""
        self.ensure_one()
        self._cl_check_open()
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only the office (Rice Mill Accountant) can settle farmers."))
        return {
            'name': self.env._("Pay Farmer: %s", self.partner_id.name),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.settlement.create',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_farmer_season_id': self.id},
        }

    def _cl_document_action(self, xml_id):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(xml_id)
        action['domain'] = [('farmer_season_id', '=', self.id)]
        action['context'] = {'default_farmer_season_id': self.id}
        return action

    def action_view_settlements(self):
        return self._cl_document_action('cl_rice_mill.cl_rice_settlement_action')

    def action_view_intakes(self):
        return self._cl_document_action('cl_rice_mill.cl_rice_intake_action')

    def action_view_milling_jobs(self):
        return self._cl_document_action('cl_rice_mill.cl_rice_milling_job_action')

    def action_view_loans(self):
        return self._cl_document_action('cl_rice_mill.cl_rice_loan_action')

    def action_view_service_jobs(self):
        return self._cl_document_action('cl_rice_mill.cl_rice_service_job_action')

    def action_view_sales(self):
        return self._cl_document_action('cl_rice_mill.cl_rice_facilitated_sale_action')

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

    def _cl_check_open_for_release(self):
        """Stock may still leave a settled season, as long as nothing is owed (no new charge is made)."""
        for fs in self.filtered('is_locked'):
            if fs.currency_id.compare_amounts(fs._cl_balance_owed(), 0) > 0:
                raise UserError(self.env._(
                    "Farmer season %s is settled with a balance still owed: reverse the settlement to release stock.",
                    fs.name))

    def _cl_advance_state(self, from_states, to_state):
        """Move farmer seasons forward when an operation happens (intake, release, settlement).

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

    def _cl_apply_credit_override(self, reason, amount, date=None):
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
