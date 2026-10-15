from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index='btree_not_null', copy=False, readonly=True)
