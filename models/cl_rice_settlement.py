from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ClRiceSettlement(models.Model):
    _name = 'cl.rice.settlement'
    _description = "Farmer Settlement"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", required=True, index=True, check_company=True, readonly=True)
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer", index=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('confirmed', "Confirmed"),
            ('paid', "Paid"),
            ('reversed', "Reversed"),
            ('cancelled', "Cancelled"),
        ],
        default='draft', required=True, tracking=True, copy=False, index=True)

    settlement_type = fields.Selection(
        [
            ('final', "Final settlement (locks the season)"),
            ('interim', "Interim payout (season stays open)"),
        ],
        string="Type", default='final', required=True, readonly=True, tracking=True)
    line_ids = fields.One2many('cl.rice.settlement.line', 'settlement_id', string="Lines")
    owed_line_ids = fields.One2many(
        'cl.rice.settlement.line', 'settlement_id',
        domain=[('line_type', '=', 'open'), ('kind', '=', 'owed')], string="Owed")
    earned_line_ids = fields.One2many(
        'cl.rice.settlement.line', 'settlement_id',
        domain=[('line_type', '=', 'open'), ('kind', '=', 'earned')], string="Earned")
    statement_line_ids = fields.One2many(
        'cl.rice.settlement.line', 'settlement_id', domain=[('line_type', '=', 'statement')],
        string="Season Statement")
    statement_charges = fields.Monetary(string="Season Charges", compute='_compute_totals', store=True)
    statement_receipts = fields.Monetary(string="Season Receipts", compute='_compute_totals', store=True)
    stock_held_kg = fields.Float(
        string="Stock Still Held (kg)", digits='Product Unit', readonly=True, copy=False,
        help="Farmer-owned stock still in the warehouse when the statement was prepared.")
    stock_override_reason = fields.Text(readonly=True, copy=False)
    stock_override_uid = fields.Many2one('res.users', string="Stock Override By", readonly=True, copy=False)
    owed_total = fields.Monetary(string="Total Owed", compute='_compute_totals', store=True)
    earned_total = fields.Monetary(string="Total Earned", compute='_compute_totals', store=True)
    net_amount = fields.Monetary(
        string="Net to Farmer", compute='_compute_totals', store=True, tracking=True,
        help="Earned minus owed. Negative: the farmer still owes the mill.")

    negative_policy = fields.Selection(
        [
            ('carry_forward', "Carry forward to next season (opening loan)"),
            ('keep', "Keep as receivable"),
        ],
        string="If the Farmer Still Owes", default='carry_forward', required=True)
    next_season_id = fields.Many2one(
        'cl.rice.season', string="Next Season", check_company=True,
        domain="[('state', '!=', 'closed'), ('id', '!=', season_id)]")
    payout_journal_id = fields.Many2one(
        'account.journal', string="Pay Out From", check_company=True, domain="[('type', 'in', ('cash', 'bank'))]")
    payout_method = fields.Selection(related='partner_id.cl_payout_method', string="Payout Method")

    move_id = fields.Many2one('account.move', string="Settlement Entry", readonly=True, copy=False)
    payment_id = fields.Many2one('account.payment', string="Payout", readonly=True, copy=False)
    carry_loan_id = fields.Many2one('cl.rice.loan', string="Carried Forward Loan", readonly=True, copy=False)
    previous_fs_state = fields.Char(readonly=True, copy=False)
    reversal_reason = fields.Text(readonly=True, copy=False)

    @api.depends('line_ids.amount', 'line_ids.kind', 'line_ids.line_type')
    def _compute_totals(self):
        for settlement in self:
            open_lines = settlement.line_ids.filtered(lambda l: l.line_type == 'open')
            statement = settlement.line_ids - open_lines
            settlement.owed_total = sum(open_lines.filtered(lambda l: l.kind == 'owed').mapped('amount'))
            settlement.earned_total = sum(open_lines.filtered(lambda l: l.kind == 'earned').mapped('amount'))
            settlement.net_amount = settlement.earned_total - settlement.owed_total
            settlement.statement_charges = sum(statement.filtered(lambda l: l.kind == 'owed').mapped('amount'))
            settlement.statement_receipts = sum(statement.filtered(lambda l: l.kind == 'earned').mapped('amount'))

    @api.depends('company_id')
    def _compute_currency_id(self):
        # the company comes from the farmer season: use the current one until it is chosen
        for record in self:
            record.currency_id = record.company_id.currency_id or self.env.company.currency_id

    @api.model_create_multi
    def create(self, vals_list):
        FarmerSeason = self.env['cl.rice.farmer.season']
        for vals in vals_list:
            company = FarmerSeason.browse(vals.get('farmer_season_id')).company_id or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.settlement') or '/'
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda s: s.state not in ('draft', 'cancelled')):
            raise UserError(self.env._("Only draft settlements can be deleted."))

    def _cl_check_accountant(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only a Rice Mill Accountant can settle farmers."))

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    # ------------------------------------------------------------------
    # Computing the statement
    # ------------------------------------------------------------------
    def action_compute(self):
        self._cl_check_accountant()
        for settlement in self:
            if settlement.state != 'draft':
                raise UserError(self.env._("Only draft settlements can be recomputed."))
            fs = settlement.farmer_season_id
            fs._cl_check_open()
            settlement.line_ids.unlink()
            statement = [dict(vals, line_type='statement') for vals in fs.sudo()._cl_statement_breakdown()]
            settlement.write({
                'line_ids': [(0, 0, vals) for vals in settlement._cl_statement_lines() + statement],
                'stock_held_kg': fs._cl_stock_held_kg(),
            })

    def _cl_open_lines(self):
        return self.line_ids.filtered(lambda l: l.line_type == 'open')

    def _cl_open_items(self):
        """Open receivable and payable items of the farmer season."""
        self.ensure_one()
        return self.env['account.move.line'].sudo().search([
            ('cl_farmer_season_id', '=', self.farmer_season_id.id),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
            ('reconciled', '=', False),
            ('amount_residual', '!=', 0),
        ], order='date, id')

    def _cl_categories(self):
        """Map journal entries of the farmer season to (settlement category, farmer-friendly label)."""
        self.ensure_one()
        fs = self.farmer_season_id.sudo()
        categories = {}
        for loan in fs.loan_ids:
            for move in loan.payment_id.move_id | loan.move_id:
                categories[move] = ('loan', fs._cl_label(loan))
        for job in fs.service_job_ids:
            categories[job.invoice_id] = ('service', fs._cl_label(job))
            categories[job.operator_move_id] = ('operator', fs._cl_label(job, 'operator'))
        for job in fs.milling_job_ids:
            categories[job.invoice_id] = ('milling', fs._cl_label(job))
        # one invoice may hold the storage of several lots
        storage = {}
        for fee in fs.storage_fee_ids:
            storage.setdefault(fee.invoice_id, []).append(fee)
        for invoice, fees in storage.items():
            label = fs._cl_label(fees[0]) if len(fees) == 1 else self.env._(
                "Storage for %(period)s (%(ref)s)", period=fees[0].period, ref=invoice.name)
            categories[invoice] = ('storage', label)
        for payment in fs.payment_ids:
            categories[payment.move_id] = ('funds_held', fs._cl_label(payment))
        for line in self.env['cl.rice.opening.balance.line'].sudo().search([('farmer_season_id', '=', fs.id)]):
            categories[line.move_id] = ('opening', fs._cl_label(line))
        categories.pop(self.env['account.move'], None)
        return categories

    def _cl_statement_lines(self):
        self.ensure_one()
        categories = self._cl_categories()
        result = []
        for sequence, item in enumerate(self._cl_open_items()):
            residual = item.amount_residual
            kind = 'owed' if residual > 0 else 'earned'
            category, label = categories.get(item.move_id) or (
                'other_charge' if kind == 'owed' else 'other_credit', item.name or item.move_id.ref or item.move_id.name)
            result.append({
                'sequence': sequence,
                'kind': kind,
                'category': category,
                'date': item.date,
                'name': label,
                'amount': abs(residual),
                'move_line_id': item.id,
            })
        return result

    # ------------------------------------------------------------------
    # Confirmation: the netting entry
    # ------------------------------------------------------------------
    def action_confirm(self):
        self._cl_check_accountant()
        for settlement in self:
            if settlement.state != 'draft':
                raise UserError(self.env._("Only draft settlements can be confirmed."))
            settlement.farmer_season_id._cl_check_open()
            if settlement.settlement_type == 'interim':
                settlement._cl_post_interim()
                continue
            open_lines = settlement._cl_open_lines()
            if not settlement.line_ids:
                raise UserError(self.env._("There is nothing to settle: compute the statement first."))
            stale = open_lines.move_line_id.filtered(lambda l: l.reconciled)
            if stale or set(settlement._cl_open_items().ids) != set(open_lines.move_line_id.ids):
                raise UserError(self.env._("The farmer's account changed since the statement was computed: recompute it."))
            held = settlement.farmer_season_id._cl_stock_held_kg()
            if held > 0 and not settlement.stock_override_reason:
                raise UserError(self.env._(
                    "%(farmer)s still has %(kg)s kg in the warehouse. Release the stock first, pay out with an "
                    "interim payout, or ask a manager to override with a reason.",
                    farmer=settlement.partner_id.name, kg=round(held, 2)))
            if settlement.currency_id.compare_amounts(settlement.net_amount, 0) < 0 \
                    and settlement.negative_policy == 'carry_forward' and not settlement.next_season_id:
                raise UserError(self.env._("Choose the next season the remaining balance is carried to."))
            settlement._cl_post_settlement()

    def _cl_post_settlement(self):
        self.ensure_one()
        company = self.company_id
        fs = self.farmer_season_id
        partner = self.partner_id
        journal = company.cl_rice_settlement_journal_id
        if not journal:
            raise UserError(self.env._("Set the settlement journal in the Rice Mill settings."))
        label = self.env._("Settlement %(ref)s - %(farmer)s", ref=self.name, farmer=partner.name)
        line_vals = []
        open_lines = self._cl_open_lines()
        for line in open_lines:
            item = line.move_line_id
            line_vals.append({
                'name': line.name,
                'account_id': item.account_id.id,
                'partner_id': item.partner_id.id,
                'debit': line.amount if line.kind == 'earned' else 0.0,
                'credit': line.amount if line.kind == 'owed' else 0.0,
                'cl_farmer_season_id': fs.id,
            })
        net = self.net_amount
        carry_fs = self.env['cl.rice.farmer.season']
        if self.currency_id.compare_amounts(net, 0) > 0:
            line_vals.append({
                'name': self.env._("Payout due - %s", label),
                'account_id': partner.with_company(company).property_account_payable_id.id,
                'partner_id': partner.id,
                'credit': net,
                'cl_farmer_season_id': fs.id,
            })
        elif self.currency_id.compare_amounts(net, 0) < 0:
            if self.negative_policy == 'carry_forward':
                carry_fs = self._cl_next_farmer_season()
                line_vals.append({
                    'name': self.env._("Balance carried forward to %s", carry_fs.season_id.name),
                    'account_id': company.cl_rice_loan_account_id.id,
                    'partner_id': partner.id,
                    'debit': -net,
                    'cl_farmer_season_id': carry_fs.id,
                })
            else:
                line_vals.append({
                    'name': self.env._("Balance still owed after settlement %s", self.name),
                    'account_id': company.cl_rice_service_account_id.id,
                    'partner_id': partner.id,
                    'debit': -net,
                    'cl_farmer_season_id': fs.id,
                })
        move = self.env['account.move']
        if line_vals:
            move = self.env['account.move'].sudo().create({
                'move_type': 'entry',
                'journal_id': journal.id,
                'date': self.date,
                'ref': label,
                'cl_farmer_season_id': fs.id,
                'company_id': company.id,
                'line_ids': [(0, 0, vals) for vals in line_vals],
            })
            move.action_post()
        # reconcile every settled item with its counterpart
        counterparts = move.line_ids.sorted('id')[:len(open_lines)]
        for line, counterpart in zip(open_lines, counterparts):
            (line.move_line_id | counterpart).reconcile()
        values = {'move_id': move.id, 'previous_fs_state': fs.state}
        if carry_fs:
            values['carry_loan_id'] = self.env['cl.rice.loan'].sudo().create({
                'farmer_season_id': carry_fs.id,
                'loan_type': 'carry_forward',
                'amount': -net,
                'purpose': self.env._("Balance carried from %s", self.name),
                'state': 'disbursed',
                'disbursement_date': self.date,
                'move_id': move.id,
            }).id
        values['state'] = 'confirmed' if self.currency_id.compare_amounts(net, 0) > 0 else 'paid'
        self.write(values)
        fs.loan_ids.filtered(lambda l: l.state == 'disbursed').sudo().state = 'settled'
        fs._cl_advance_state(('active', 'harvested', 'in_storage', 'released'), 'settled')
        self.message_post(body=self.env._(
            "Settlement confirmed by %(user)s: earned %(earned)s, owed %(owed)s, net %(net)s.",
            user=self.env.user.name,
            earned=self.currency_id.format(self.earned_total),
            owed=self.currency_id.format(self.owed_total),
            net=self.currency_id.format(net)))
        if self.state == 'paid':
            partner._cl_rice_notify('settlement_paid', self)

    def _cl_post_interim(self):
        """Interim payout: the money held first pays the farmer's balance, the rest is paid out;
        the season stays open."""
        self.ensure_one()
        fs = self.farmer_season_id
        if not self.payout_journal_id:
            raise UserError(self.env._("Choose the journal the farmer is paid from."))
        applied = fs.sudo()._cl_apply_funds_held()
        application = self.env['account.move']
        if applied:
            application = self.env['account.move'].sudo().search(
                [('cl_farmer_season_id', '=', fs.id), ('journal_id', '=', self.company_id.cl_rice_settlement_journal_id.id)],
                order='id desc', limit=1)
        funds_account = self.company_id.cl_rice_funds_held_account_id
        funds_lines = self.env['account.move.line'].sudo().search([
            ('cl_farmer_season_id', '=', fs.id),
            ('parent_state', '=', 'posted'),
            ('account_id', '=', funds_account.id),
            ('reconciled', '=', False),
            ('amount_residual', '<', 0),
        ])
        amount = -sum(funds_lines.mapped('amount_residual'))
        if self.currency_id.compare_amounts(amount, 0) <= 0:
            raise UserError(self.env._(
                "Nothing can be paid out: the money held for %s does not cover what he owes.", self.partner_id.name))
        payment = self._cl_create_payout(amount, funds_account)
        (payment.move_id.line_ids.filtered(lambda l: l.account_id == funds_account) | funds_lines).reconcile()
        self.write({'state': 'paid', 'payment_id': payment.id, 'move_id': application.id})
        self.message_post(body=self.env._(
            "Interim payout of %(amount)s by %(user)s (%(applied)s first applied to the balance).",
            amount=self.currency_id.format(amount), user=self.env.user.name,
            applied=self.currency_id.format(applied)))
        self.partner_id._cl_rice_notify('settlement_paid', self)

    def _cl_next_farmer_season(self):
        self.ensure_one()
        FarmerSeason = self.env['cl.rice.farmer.season'].sudo()
        next_fs = FarmerSeason.search([
            ('partner_id', '=', self.partner_id.id),
            ('season_id', '=', self.next_season_id.id),
        ], limit=1)
        if not next_fs:
            next_fs = FarmerSeason.create({
                'partner_id': self.partner_id.id,
                'season_id': self.next_season_id.id,
                'company_id': self.company_id.id,
                'field_officer_id': self.farmer_season_id.field_officer_id.id,
            })
        if next_fs.state == 'draft':
            next_fs.action_activate()
        next_fs._cl_check_open()
        return next_fs

    # ------------------------------------------------------------------
    # Payout
    # ------------------------------------------------------------------
    def action_pay(self):
        self._cl_check_accountant()
        for settlement in self:
            if settlement.state != 'confirmed':
                raise UserError(self.env._("Only confirmed settlements with money due to the farmer can be paid."))
            if not settlement.payout_journal_id:
                raise UserError(self.env._("Choose the journal the farmer is paid from."))
            settlement._cl_pay()

    def _cl_pay(self):
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id
        payable_account = partner.with_company(company).property_account_payable_id
        payout_line = self.move_id.line_ids.filtered(
            lambda l: l.account_id == payable_account and l.credit and not l.reconciled)
        payment = self._cl_create_payout(self.net_amount, payable_account)
        (payment.move_id.line_ids.filtered(lambda l: l.account_id == payable_account) | payout_line).reconcile()
        self.write({'payment_id': payment.id, 'state': 'paid'})
        self.message_post(body=self.env._(
            "Payout of %(amount)s made by %(user)s from %(journal)s.",
            amount=self.currency_id.format(self.net_amount), user=self.env.user.name,
            journal=self.payout_journal_id.name))
        partner._cl_rice_notify('settlement_paid', self)

    def _cl_create_payout(self, amount, account):
        self.ensure_one()
        partner = self.partner_id.sudo()
        memo = self.env._("Settlement %(ref)s payout (%(method)s %(phone)s)",
                          ref=self.name,
                          method=dict(partner._fields['cl_payout_method']._description_selection(self.env)).get(
                              partner.cl_payout_method, ''),
                          phone=partner.cl_payout_phone or '')
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': partner.id,
            'amount': amount,
            'date': fields.Date.context_today(self),
            'journal_id': self.payout_journal_id.id,
            'payment_method_line_id': self.payout_journal_id._cl_outbound_method_line().id,
            'destination_account_id': account.id,
            'partner_bank_id': partner.cl_payout_bank_id.id if partner.cl_payout_method == 'bank' else False,
            'memo': memo,
            'company_id': self.company_id.id,
        })
        payment.action_post()
        return payment

    # ------------------------------------------------------------------
    # Corrections
    # ------------------------------------------------------------------
    def action_cancel(self):
        if self.filtered(lambda s: s.state != 'draft'):
            raise UserError(self.env._("Only draft settlements can be cancelled; reverse the others."))
        self.state = 'cancelled'

    def action_open_reverse(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Reverse Settlement"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_action': 'reverse'},
        }

    def _cl_reason_actions(self):
        return {'reverse': self._cl_reverse, 'stock_override': self._cl_stock_override}

    def action_open_stock_override(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Settle Although Stock Is Held"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_action': 'stock_override'},
        }

    def _cl_stock_override(self, reason, amount=None, date=None):
        self.ensure_one()
        self._cl_check_manager()
        if self.state != 'draft':
            raise UserError(self.env._("Only a draft settlement can be overridden."))
        self.write({'stock_override_reason': reason, 'stock_override_uid': self.env.uid})
        self.message_post(body=self.env._(
            "Final settlement allowed by %(user)s although %(kg)s kg are still held. Reason: %(reason)s",
            user=self.env.user.name, kg=round(self.stock_held_kg, 2), reason=reason))

    def _cl_reverse(self, reason, amount=None, date=None):
        """Undo a settlement: the payout and the netting entry are reversed and the farmer season reopens."""
        self.ensure_one()
        self._cl_check_manager()
        if self.state not in ('confirmed', 'paid'):
            raise UserError(self.env._("Only confirmed or paid settlements can be reversed."))
        if self.carry_loan_id and self.carry_loan_id.farmer_season_id.is_locked:
            raise UserError(self.env._("The balance carried forward is already settled in the next season."))
        if self.payment_id and self.payment_id.state != 'canceled':
            self.payment_id.move_id.line_ids.remove_move_reconcile()
            self.payment_id.action_draft()
            self.payment_id.action_cancel()
        if self.move_id.state == 'posted':
            self.move_id.line_ids.remove_move_reconcile()
            self.move_id._reverse_moves(
                [{'ref': self.env._("Reversal of %(ref)s: %(reason)s", ref=self.move_id.name, reason=reason)}],
                cancel=True)
        if self.carry_loan_id:
            self.carry_loan_id.sudo().state = 'cancelled'
        fs = self.farmer_season_id
        if self.settlement_type == 'final':
            fs.loan_ids.filtered(lambda l: l.state == 'settled').sudo().state = 'disbursed'
            fs.sudo().with_context(cl_rice_unlock=True).state = self.previous_fs_state or 'released'
        self.write({'state': 'reversed', 'reversal_reason': reason})
        message = self.env._(
            "Settlement reversed by %(user)s. Reason: %(reason)s", user=self.env.user.name, reason=reason)
        self.message_post(body=message)
        fs.message_post(body=message)
