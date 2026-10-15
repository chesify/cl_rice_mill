from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index='btree_not_null',
        help="Farmer season this journal item is owed by or due to. Drives the farmer statement and the settlement.")
    cl_proceeds_ids = fields.One2many('cl.rice.proceeds', 'invoice_line_id', string="Farmer Proceeds")
