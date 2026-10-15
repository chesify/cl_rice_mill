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
    currency_id = fields.Many2one(related='company_id.currency_id')
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

    line_ids = fields.One2many('cl.rice.settlement.line', 'settlement_id', string="Lines")
    owed_line_ids = fields.One2many(
        'cl.rice.settlement.line', 'settlement_id', domain=[('kind', '=', 'owed')], string="Owed")
    earned_line_ids = fields.One2many(
        'cl.rice.settlement.line', 'settlement_id', domain=[('kind', '=', 'earned')], string="Earned")
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

    @api.depends('line_ids.amount', 'line_ids.kind')
    def _compute_totals(self):
        for settlement in self:
            settlement.owed_total = sum(settlement.line_ids.filtered(lambda l: l.kind == 'owed').mapped('amount'))
            settlement.earned_total = sum(settlement.line_ids.filtered(lambda l: l.kind == 'earned').mapped('amount'))
            settlement.net_amount = settlement.earned_total - settlement.owed_total

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
            settlement.line_ids = [(0, 0, vals) for vals in settlement._cl_statement_lines()]

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
        """Map journal entries of the farmer season to settlement categories."""
        self.ensure_one()
        fs = self.farmer_season_id.sudo()
        categories = {}
        for loan in fs.loan_ids:
            for move in loan.payment_id.move_id | loan.move_id:
                categories[move] = 'loan'
        for job in fs.service_job_ids:
            categories[job.invoice_id] = 'service'
            categories[job.operator_move_id] = 'operator'
        for job in fs.milling_job_ids:
            categories[job.invoice_id] = 'milling'
        for fee in fs.storage_fee_ids:
            categories[fee.invoice_id] = 'storage'
        for payment in fs.payment_ids:
            categories[payment.move_id] = 'funds_held'
        for line in self.env['cl.rice.opening.balance.line'].sudo().search([('farmer_season_id', '=', fs.id)]):
            categories[line.move_id] = 'opening'
        return categories

    def _cl_statement_lines(self):
        self.ensure_one()
        categories = self._cl_categories()
        result = []
        for sequence, item in enumerate(self._cl_open_items()):
            residual = item.amount_residual
            kind = 'owed' if residual > 0 else 'earned'
            category = categories.get(item.move_id) or ('other_charge' if kind == 'owed' else 'other_credit')
            result.append({
                'sequence': sequence,
                'kind': kind,
                'category': category,
                'date': item.date,
                'name': item.name or item.move_id.ref or item.move_id.name,
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
            if not settlement.line_ids:
                raise UserError(self.env._("There is nothing to settle: compute the statement first."))
            stale = settlement.line_ids.move_line_id.filtered(lambda l: l.reconciled)
            if stale or set(settlement._cl_open_items().ids) != set(settlement.line_ids.move_line_id.ids):
                raise UserError(self.env._("The farmer's account changed since the statement was computed: recompute it."))
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
        for line in self.line_ids:
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
        counterparts = move.line_ids.sorted('id')[:len(self.line_ids)]
        for line, counterpart in zip(self.line_ids, counterparts):
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
        sudo_partner = partner.sudo()
        memo = self.env._("Settlement %(ref)s payout (%(method)s %(phone)s)",
                          ref=self.name,
                          method=dict(sudo_partner._fields['cl_payout_method']._description_selection(self.env)).get(
                              sudo_partner.cl_payout_method, ''),
                          phone=sudo_partner.cl_payout_phone or '')
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': partner.id,
            'amount': self.net_amount,
            'date': fields.Date.context_today(self),
            'journal_id': self.payout_journal_id.id,
            'payment_method_line_id': self.payout_journal_id._cl_outbound_method_line().id,
            'destination_account_id': payable_account.id,
            'partner_bank_id': sudo_partner.cl_payout_bank_id.id if sudo_partner.cl_payout_method == 'bank' else False,
            'memo': memo,
            'company_id': company.id,
        })
        payment.action_post()
        (payment.move_id.line_ids.filtered(lambda l: l.account_id == payable_account) | payout_line).reconcile()
        self.write({'payment_id': payment.id, 'state': 'paid'})
        self.message_post(body=self.env._(
            "Payout of %(amount)s made by %(user)s from %(journal)s.",
            amount=self.currency_id.format(self.net_amount), user=self.env.user.name,
            journal=self.payout_journal_id.name))
        partner._cl_rice_notify('settlement_paid', self)

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
        return {'reverse': self._cl_reverse}

    def _cl_reverse(self, reason, amount=None):
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
        fs.loan_ids.filtered(lambda l: l.state == 'settled').sudo().state = 'disbursed'
        fs.sudo().with_context(cl_rice_unlock=True).state = self.previous_fs_state or 'released'
        self.write({'state': 'reversed', 'reversal_reason': reason})
        message = self.env._(
            "Settlement reversed by %(user)s. Reason: %(reason)s", user=self.env.user.name, reason=reason)
        self.message_post(body=message)
        fs.message_post(body=message)
