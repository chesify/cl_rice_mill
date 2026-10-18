from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ClRiceFarmerPayment(models.Model):
    _name = 'cl.rice.farmer.payment'
    _description = "Payment Received for a Farmer"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    payment_type = fields.Selection(
        [
            ('repayment', "Farmer pays his balance"),
            ('buyer', "Buyer pays the mill for the farmer"),
        ],
        string="Type", required=True, default='repayment', tracking=True)
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", required=True, index=True, tracking=True,
        check_company=True, domain="[('state', 'not in', ('draft', 'settled', 'closed'))]")
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer", index=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    sale_id = fields.Many2one(
        'cl.rice.facilitated.sale', string="Facilitated Sale", index='btree_not_null', check_company=True,
        domain="[('farmer_season_id', '=', farmer_season_id), ('payment_route', '=', 'luvanda')]")
    payer_id = fields.Many2one(
        'res.partner', string="Paid By", compute='_compute_payer_id', store=True, readonly=False, precompute=True)
    amount = fields.Monetary(required=True, tracking=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    journal_id = fields.Many2one(
        'account.journal', string="Received Into", required=True, check_company=True,
        domain="[('type', 'in', ('cash', 'bank'))]", help="Cash, bank or mobile money (M-Pesa, Tigo Pesa, Airtel Money).")
    reference = fields.Char(string="Payment Reference", help="Mobile money transaction code, bank slip or receipt number.")
    state = fields.Selection(
        [('draft', "Draft"), ('posted', "Received"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False, index=True)
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    applied_amount = fields.Monetary(
        string="Applied to Balance", readonly=True, copy=False,
        help="Part of a farmer's payment that paid his open balance; the rest is held for him.")

    _amount_positive = models.Constraint('CHECK(amount > 0)', "The amount must be positive.")

    @api.depends('payment_type', 'sale_id', 'farmer_season_id')
    def _compute_payer_id(self):
        for payment in self:
            if payment.payment_type == 'buyer':
                payment.payer_id = payment.sale_id.buyer_id or payment.payer_id
            else:
                payment.payer_id = payment.farmer_season_id.partner_id

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
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.farmer.payment') or '/'
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) - {'reference', 'state', 'move_id', 'applied_amount'} \
                and self.filtered(lambda p: p.state != 'draft'):
            raise UserError(self.env._("A received payment cannot be changed. Cancel it with a reason instead."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda p: p.state == 'posted'):
            raise UserError(self.env._("A received payment cannot be deleted."))

    def action_post(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only the office (Rice Mill Accountant) can receive payments."))
        for payment in self:
            if payment.state != 'draft':
                raise UserError(self.env._("Payment %s is already processed.", payment.name))
            payment.farmer_season_id._cl_check_open()
            if payment.payment_type == 'buyer' and not payment.payer_id:
                raise UserError(self.env._("Enter the buyer who paid."))
            payment._cl_post()

    def _cl_post(self):
        self.ensure_one()
        company = self.company_id
        fs = self.farmer_season_id
        outstanding = self.journal_id._cl_method_line('inbound').payment_account_id
        if not company.cl_rice_funds_held_account_id or not company.cl_rice_settlement_journal_id:
            raise UserError(self.env._("Run the Rice Mill setup first: the farmer accounts are missing."))
        if self.payment_type == 'repayment':
            ref = self.env._("Payment %(ref)s from %(farmer)s", ref=self.name, farmer=self.partner_id.name)
            move, applied = fs._cl_post_allocation(
                self.journal_id,
                {'account_id': outstanding.id, 'partner_id': self.partner_id.id, 'name': ref},
                self.amount, ref, date=self.date, excess_to_funds=True,
            )
        else:
            ref = self.env._(
                "Payment %(ref)s from %(buyer)s for %(farmer)s",
                ref=self.name, buyer=self.payer_id.name, farmer=self.partner_id.name)
            move = self.env['account.move'].sudo().create({
                'move_type': 'entry',
                'journal_id': self.journal_id.id,
                'date': self.date,
                'ref': ref,
                'cl_farmer_season_id': fs.id,
                'company_id': company.id,
                'line_ids': [
                    (0, 0, {
                        'name': ref,
                        'account_id': outstanding.id,
                        'partner_id': self.payer_id.id,
                        'debit': self.amount,
                    }),
                    (0, 0, {
                        'name': ref,
                        'account_id': company.cl_rice_funds_held_account_id.id,
                        'partner_id': self.partner_id.id,
                        'credit': self.amount,
                        'cl_farmer_season_id': fs.id,
                    }),
                ],
            })
            move.action_post()
            applied = 0.0
        self.write({'state': 'posted', 'move_id': move.id, 'applied_amount': applied})
        self.message_post(body=self.env._(
            "%(amount)s received by %(user)s into %(journal)s.",
            amount=self.currency_id.format(self.amount), user=self.env.user.name, journal=self.journal_id.name))
        self.partner_id._cl_rice_notify('payment_received', self)

    def action_open_reverse(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Cancel Payment"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_action': 'reverse'},
        }

    def action_cancel(self):
        if self.filtered(lambda p: p.state == 'posted'):
            raise UserError(self.env._("A received payment can only be cancelled by a manager, with a reason."))
        self.state = 'cancelled'

    def _cl_reverse(self, reason, amount=None, date=None):
        self.ensure_one()
        self._cl_check_manager()
        if self.state != 'posted':
            raise UserError(self.env._("Only received payments can be cancelled."))
        self.farmer_season_id._cl_check_open()
        if self.move_id.state == 'posted':
            self.move_id.line_ids.remove_move_reconcile()
            self.move_id._reverse_moves(
                [{'ref': self.env._("Reversal of %(ref)s: %(reason)s", ref=self.move_id.name, reason=reason)}],
                cancel=True)
        self.state = 'cancelled'
        self.message_post(body=self.env._(
            "Payment cancelled by %(user)s. Reason: %(reason)s", user=self.env.user.name, reason=reason))

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_reason_actions(self):
        return {'reverse': self._cl_reverse}
