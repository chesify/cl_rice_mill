from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        farmer_lines = self.order_line.filtered('cl_farmer_season_id')
        # owner mode: the mill buys the crop from the farmer before selling it
        farmer_lines.filtered(lambda l: l.cl_sale_mode == 'owner')._cl_buy_from_farmer()
        farmer_lines.filtered(lambda l: l.cl_sale_mode == 'agent')._cl_check_farmer_stock()
        return super().action_confirm()
