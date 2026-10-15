from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

LOAN_TYPES = [
    ('land_prep', "Land Preparation"),
    ('harvest', "Harvest"),
    ('other', "Other"),
    ('carry_forward', "Balance Carried Forward"),
]


class ClRiceLoan(models.Model):
    _name = 'cl.rice.loan'
    _description = "Farmer Loan (Mkopo)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", required=True, index=True, tracking=True,
        check_company=True, domain="[('state', 'not in', ('settled', 'closed'))]")
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer", index=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    loan_type = fields.Selection(LOAN_TYPES, string="Type", required=True, default='land_prep', tracking=True)
    purpose = fields.Char()
    amount = fields.Monetary(required=True, tracking=True)
    date = fields.Date(string="Request Date", required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('to_approve', "To Approve"),
            ('approved', "Approved"),
            ('disbursed', "Disbursed"),
            ('settled', "Settled"),
            ('cancelled', "Cancelled"),
        ],
        default='draft', required=True, tracking=True, copy=False, index=True)

    # interest terms, copied from the company configuration when the loan is created
    interest_mode = fields.Selection(
        [('none', "No interest"), ('flat', "Flat % per loan"), ('monthly', "Simple monthly %")],
        readonly=True, copy=False)
    interest_rate = fields.Float(string="Interest Rate (%)", digits=(16, 4), readonly=True, copy=False)
    interest_accrual = fields.Selection(
        [('cron', "Accrue monthly (scheduled)"), ('settlement', "Calculate at settlement")],
        readonly=True, copy=False)
    interest_ids = fields.One2many('cl.rice.loan.interest', 'loan_id', string="Interest")
    interest_total = fields.Monetary(compute='_compute_interest_total', store=True)

    # approval
    over_limit = fields.Boolean(compute='_compute_over_limit', help="The loan takes the farmer over the credit limit.")
    approved_uid = fields.Many2one('res.users', string="Approved By", readonly=True, copy=False, tracking=True)
    approval_reason = fields.Text(readonly=True, copy=False)

    # disbursement
    disbursement_method = fields.Selection(
        [('cash', "Cash"), ('bank', "Bank"), ('mobile_money', "Mobile Money")],
        default='cash', tracking=True)
    journal_id = fields.Many2one(
        'account.journal', string="Paid From", check_company=True,
        domain="[('type', 'in', ('cash', 'bank'))]", tracking=True)
    disbursement_date = fields.Date(readonly=True, copy=False, tracking=True)
    payment_id = fields.Many2one('account.payment', string="Payment", readonly=True, copy=False)
    move_id = fields.Many2one(
        'account.move', string="Journal Entry", readonly=True, copy=False,
        help="Entry of a balance carried forward (no cash is paid out).")

    _amount_positive = models.Constraint('CHECK(amount > 0)', "The loan amount must be positive.")

    @api.depends('interest_ids.amount')
    def _compute_interest_total(self):
        for loan in self:
            loan.interest_total = sum(loan.interest_ids.mapped('amount'))

    @api.depends('amount', 'state', 'farmer_season_id.credit_limit')
    def _compute_over_limit(self):
        for loan in self:
            fs = loan.farmer_season_id
            if loan.state in ('draft', 'to_approve', 'approved') and fs:
                exposure = fs._cl_exposure(exclude_loans=loan._origin) + loan.amount
                loan.over_limit = fs.currency_id.compare_amounts(exposure, fs.credit_limit) > 0
            else:
                loan.over_limit = False

    @api.model_create_multi
    def create(self, vals_list):
        FarmerSeason = self.env['cl.rice.farmer.season']
        for vals in vals_list:
            fs = FarmerSeason.browse(vals.get('farmer_season_id'))
            company = fs.company_id or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.loan') or '/'
            if vals.get('loan_type') != 'carry_forward':
                vals.update({
                    'interest_mode': company.cl_rice_interest_mode,
                    'interest_rate': company.cl_rice_interest_rate,
                    'interest_accrual': company.cl_rice_interest_accrual,
                })
            else:
                vals.update({'interest_mode': 'none', 'interest_rate': 0.0})
        loans = super().create(vals_list)
        loans.farmer_season_id._cl_check_open()
        return loans

    def write(self, vals):
        protected = {'amount', 'farmer_season_id', 'loan_type'}
        if protected & set(vals) and self.filtered(lambda l: l.state not in ('draft', 'to_approve')):
            raise UserError(self.env._("An approved loan cannot be changed. Cancel it and create a new one."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda l: l.state not in ('draft', 'cancelled')):
            raise UserError(self.env._("Only draft or cancelled loans can be deleted."))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        for loan in self:
            if loan.state != 'draft':
                raise UserError(self.env._("Only draft loans can be submitted."))
            loan.farmer_season_id._cl_check_open()
            if loan.farmer_season_id.state == 'draft':
                raise UserError(self.env._("Activate the farmer season before requesting a loan."))
        self.state = 'to_approve'
        managers = self.env.ref('cl_rice_mill.cl_rice_mill_group_manager').all_user_ids
        for loan in self:
            user = managers[:1]
            if user:
                loan.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=user.id,
                    summary=self.env._("Approve loan %s", loan.name))

    def action_approve(self):
        self._cl_check_manager()
        over_limit = self.filtered('over_limit')
        if over_limit:
            if len(self) > 1:
                raise UserError(self.env._("Loans above the credit limit must be approved one by one, with a reason."))
            return {
                'name': self.env._("Approve Above Credit Limit"),
                'type': 'ir.actions.act_window',
                'res_model': 'cl.rice.reason',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_res_model': self._name,
                    'default_res_id': self.id,
                    'default_action': 'approve_over_limit',
                },
            }
        self._cl_approve(reason=False)
        return True

    def _cl_approve(self, reason, amount=None):
        self._cl_check_manager()
        for loan in self:
            if loan.state != 'to_approve':
                raise UserError(self.env._("Only loans waiting for approval can be approved."))
            loan.farmer_season_id._cl_check_open()
        self.write({'state': 'approved', 'approved_uid': self.env.uid, 'approval_reason': reason})
        for loan in self:
            body = self.env._("Loan approved by %s.", self.env.user.name)
            if reason:
                body = self.env._(
                    "Loan approved above the credit limit by %(user)s. Reason: %(reason)s",
                    user=self.env.user.name, reason=reason)
            loan.message_post(body=body)
        self.activity_feedback(['mail.mail_activity_data_todo'])

    def action_disburse(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only a Rice Mill Accountant can disburse loans."))
        for loan in self:
            if loan.state != 'approved':
                raise UserError(self.env._("Only approved loans can be disbursed."))
            loan.farmer_season_id._cl_check_open()
            if loan.over_limit and not loan.approval_reason:
                raise UserError(self.env._(
                    "Loan %s is above the credit limit and must be approved again by a manager.", loan.name))
            loan._cl_disburse()

    def _cl_disburse(self):
        self.ensure_one()
        company = self.company_id
        if not company.cl_rice_loan_account_id:
            raise UserError(self.env._("Set the farmer loans receivable account in the Rice Mill settings."))
        if not self.journal_id:
            raise UserError(self.env._("Choose the journal the loan is paid from."))
        method_line = self.journal_id._cl_outbound_method_line()
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'outbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'date': fields.Date.context_today(self),
            'journal_id': self.journal_id.id,
            'payment_method_line_id': method_line.id,
            'destination_account_id': company.cl_rice_loan_account_id.id,
            'memo': self.env._("Loan %(loan)s - %(fs)s", loan=self.name, fs=self.farmer_season_id.name),
            'company_id': company.id,
        })
        payment.action_post()
        payment.move_id.cl_farmer_season_id = self.farmer_season_id
        payment.move_id.line_ids.filtered(
            lambda l: l.account_id == company.cl_rice_loan_account_id
        ).cl_farmer_season_id = self.farmer_season_id
        self.write({
            'state': 'disbursed',
            'payment_id': payment.id,
            'disbursement_date': payment.date,
        })
        if self.interest_mode == 'flat' and self.interest_rate:
            self._cl_post_interest([('FLAT', self.currency_id.round(self.amount * self.interest_rate / 100.0))])
        self.message_post(body=self.env._(
            "Loan of %(amount)s disbursed by %(user)s from %(journal)s.",
            amount=self.currency_id.format(self.amount), user=self.env.user.name, journal=self.journal_id.name))
        self.partner_id._cl_rice_notify('loan_disbursed', self)

    def action_cancel(self):
        for loan in self:
            if loan.state in ('settled', 'cancelled'):
                raise UserError(self.env._("Loan %s cannot be cancelled.", loan.name))
            if loan.state == 'disbursed':
                raise UserError(self.env._("A disbursed loan can only be reversed by a manager, with a reason."))
        self.state = 'cancelled'

    def action_open_reverse(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Reverse Loan"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_action': 'reverse',
            },
        }

    def _cl_reverse(self, reason, amount=None):
        self.ensure_one()
        self._cl_check_manager()
        if self.state != 'disbursed':
            raise UserError(self.env._("Only disbursed loans can be reversed."))
        self.farmer_season_id._cl_check_open()
        moves = self.payment_id.move_id | self.move_id | self.interest_ids.move_id
        moves = moves.filtered(lambda m: m.state == 'posted')
        if moves:
            moves.line_ids.remove_move_reconcile()
            reversals = moves._reverse_moves(
                [{'ref': self.env._("Reversal of %(ref)s: %(reason)s", ref=move.name, reason=reason)} for move in moves],
                cancel=True)
            reversals.line_ids.filtered(lambda l: l.account_id.reconcile).cl_farmer_season_id = self.farmer_season_id
        self.state = 'cancelled'
        self.message_post(body=self.env._(
            "Loan reversed by %(user)s. Reason: %(reason)s", user=self.env.user.name, reason=reason))

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_reason_actions(self):
        return {
            'approve_over_limit': self._cl_approve,
            'reverse': self._cl_reverse,
        }

    # ------------------------------------------------------------------
    # Interest
    # ------------------------------------------------------------------
    def _cl_post_interest(self, periods):
        """Post interest for (period key, amount) pairs not posted yet. Returns the new interest records."""
        self.ensure_one()
        company = self.company_id
        if not company.cl_rice_interest_income_account_id or not company.cl_rice_loan_journal_id:
            raise UserError(self.env._("Set the interest income account and the loans journal in the Rice Mill settings."))
        done = set(self.interest_ids.mapped('period'))
        periods = [(key, amount) for key, amount in periods if key not in done and amount > 0]
        if not periods:
            return self.env['cl.rice.loan.interest']
        fs = self.farmer_season_id
        distribution = fs._cl_analytic_distribution(self.env.ref('cl_rice_mill.analytic_service_interest'))
        line_vals = []
        for key, amount in periods:
            label = self.env._("Interest %(period)s on loan %(loan)s", period=key, loan=self.name)
            line_vals += [
                {
                    'name': label,
                    'account_id': company.cl_rice_loan_account_id.id,
                    'partner_id': self.partner_id.id,
                    'debit': amount,
                    'cl_farmer_season_id': fs.id,
                },
                {
                    'name': label,
                    'account_id': company.cl_rice_interest_income_account_id.id,
                    'partner_id': self.partner_id.id,
                    'credit': amount,
                    'analytic_distribution': distribution,
                },
            ]
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': company.cl_rice_loan_journal_id.id,
            'date': fields.Date.context_today(self),
            'ref': self.env._("Interest on loan %s", self.name),
            'cl_farmer_season_id': fs.id,
            'company_id': company.id,
            'line_ids': [(0, 0, vals) for vals in line_vals],
        })
        move.action_post()
        return self.env['cl.rice.loan.interest'].create([
            {'loan_id': self.id, 'period': key, 'amount': amount, 'move_id': move.id}
            for key, amount in periods
        ])

    def _cl_monthly_interest_periods(self, until, started_months=False):
        """Monthly interest periods due until ``until``.

        :param started_months: count a started month as a full month (used at settlement);
            otherwise only complete months are counted (used by the monthly accrual).
        """
        self.ensure_one()
        if self.interest_mode != 'monthly' or not self.interest_rate or not self.disbursement_date:
            return []
        start = self.disbursement_date
        months = (until.year - start.year) * 12 + until.month - start.month
        if start + relativedelta(months=months) > until:
            months -= 1
        if started_months and start + relativedelta(months=months) < until:
            months += 1
        months = max(months, 0)
        monthly = self.currency_id.round(self.amount * self.interest_rate / 100.0)
        return [(f"M{index:02d}", monthly) for index in range(1, months + 1)]

    @api.model
    def _cron_accrue_interest(self):
        today = fields.Date.context_today(self)
        loans = self.search([
            ('state', '=', 'disbursed'),
            ('interest_mode', '=', 'monthly'),
            ('interest_accrual', '=', 'cron'),
            ('farmer_season_id.state', 'not in', ('settled', 'closed')),
        ])
        for loan in loans:
            loan._cl_post_interest(loan._cl_monthly_interest_periods(today))

    def _cl_accrue_interest_at_settlement(self, date):
        for loan in self.filtered(lambda l: l.state == 'disbursed' and l.interest_mode == 'monthly'):
            loan._cl_post_interest(loan._cl_monthly_interest_periods(date, started_months=True))


    def action_open_payment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'res_id': self.payment_id.id,
            'view_mode': 'form',
        }
