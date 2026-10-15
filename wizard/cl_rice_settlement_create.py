from odoo import fields, models
from odoo.exceptions import UserError


class ClRiceSettlementCreate(models.TransientModel):
    _name = 'cl.rice.settlement.create'
    _description = "Settle a Farmer Season"

    farmer_season_id = fields.Many2one('cl.rice.farmer.season', required=True, readonly=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
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

    def action_create(self):
        self.ensure_one()
        fs = self.farmer_season_id
        fs._cl_check_open()
        if fs.settlement_ids.filtered(lambda s: s.state in ('draft', 'confirmed')):
            raise UserError(self.env._("Farmer season %s already has a settlement in progress.", fs.name))
        settlement = self.env['cl.rice.settlement'].create({
            'farmer_season_id': fs.id,
            'date': self.date,
            'negative_policy': self.negative_policy,
            'next_season_id': self.next_season_id.id,
            'payout_journal_id': self.payout_journal_id.id,
        })
        settlement.action_compute()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.settlement',
            'res_id': settlement.id,
            'view_mode': 'form',
        }
