from odoo import api, fields, models
from odoo.exceptions import UserError


class ClRiceSettlementCreate(models.TransientModel):
    _name = 'cl.rice.settlement.create'
    _description = "Settle a Farmer Season"

    farmer_season_id = fields.Many2one('cl.rice.farmer.season', required=True, readonly=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    settlement_type = fields.Selection(
        [
            ('final', "Final settlement (locks the season)"),
            ('interim', "Interim payout (season stays open)"),
        ],
        string="Type", default='final', required=True)
    stock_held_kg = fields.Float(string="Stock Still Held (kg)", related='farmer_season_id.stock_held_kg')
    negative_policy = fields.Selection(
        [
            ('carry_forward', "Carry forward to next season (opening loan)"),
            ('keep', "Keep as receivable"),
        ],
        string="If the Farmer Still Owes", default='carry_forward', required=True)
    next_season_id = fields.Many2one(
        'cl.rice.season', string="Next Season",
        domain="[('state', '!=', 'closed'), ('company_id', '=', company_id)]")
    payout_journal_id = fields.Many2one(
        'account.journal', string="Pay Out From",
        domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id)]")

    # interim payout: split of the money held between the farmer and the debt
    currency_id = fields.Many2one(related='company_id.currency_id')
    funds_held = fields.Monetary(string="Funds Held", compute='_compute_balances')
    balance_owed = fields.Monetary(string="Owed by the Farmer", compute='_compute_balances')
    payout_amount = fields.Monetary(
        string="Amount to Pay the Farmer", compute='_compute_payout_amount', store=True, readonly=False,
        help="Paid out through the chosen journal. By default what is left once the debt is paid.")
    applied_amount = fields.Monetary(
        string="Applied to the Debt", compute='_compute_split',
        help="Funds held minus the amount paid out, applied to the oldest open items.")
    kept_amount = fields.Monetary(string="Kept as Funds Held", compute='_compute_split')
    owed_after = fields.Monetary(string="Still Owed After Payout", compute='_compute_split')
    cover_after = fields.Float(string="Collateral Cover After Payout (%)", compute='_compute_split', digits=(16, 1))
    needs_debt_approval = fields.Boolean(compute='_compute_split')

    @api.depends('farmer_season_id')
    def _compute_balances(self):
        for wizard in self:
            fs = wizard.farmer_season_id.sudo()
            wizard.funds_held = fs._cl_funds_held_available() if fs else 0.0
            wizard.balance_owed = fs._cl_balance_owed() if fs else 0.0

    @api.depends('settlement_type', 'funds_held', 'balance_owed')
    def _compute_payout_amount(self):
        for wizard in self:
            if wizard.settlement_type == 'interim':
                wizard.payout_amount = max(wizard.funds_held - wizard.balance_owed, 0.0)
            else:
                wizard.payout_amount = 0.0

    @api.depends('funds_held', 'balance_owed', 'payout_amount')
    def _compute_split(self):
        for wizard in self:
            fs = wizard.farmer_season_id
            split = fs._cl_payout_split(wizard.funds_held, wizard.balance_owed, wizard.payout_amount)
            wizard.update(split)
            wizard.cover_after = fs.sudo()._cl_cover_for(split['owed_after'])

    def action_create(self):
        self.ensure_one()
        fs = self.farmer_season_id
        fs._cl_check_open()
        if fs.settlement_ids.filtered(lambda s: s.state in ('draft', 'confirmed')):
            raise UserError(self.env._("Farmer season %s already has a settlement in progress.", fs.name))
        if self.settlement_type == 'interim':
            currency = self.currency_id
            if currency.compare_amounts(self.payout_amount, 0) < 0:
                raise UserError(self.env._("The amount to pay the farmer cannot be negative."))
            if currency.compare_amounts(self.payout_amount, self.funds_held) > 0:
                raise UserError(self.env._(
                    "Only %(held)s is held for the farmer: pay out at most that.",
                    held=currency.format(self.funds_held)))
        settlement = self.env['cl.rice.settlement'].create({
            'farmer_season_id': fs.id,
            'date': self.date,
            'settlement_type': self.settlement_type,
            'negative_policy': self.negative_policy,
            'next_season_id': self.next_season_id.id,
            'payout_journal_id': self.payout_journal_id.id,
            'payout_amount': self.payout_amount if self.settlement_type == 'interim' else 0.0,
        })
        settlement.action_compute()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.settlement',
            'res_id': settlement.id,
            'view_mode': 'form',
        }
