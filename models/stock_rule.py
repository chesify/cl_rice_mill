from odoo import models


class StockRule(models.Model):
    _inherit = 'stock.rule'

    def _get_custom_move_fields(self):
        # farmer sales: deliver the farmer's own lot (and only their stock in agent mode)
        return super()._get_custom_move_fields() + ['cl_lot_id', 'restrict_partner_id']
