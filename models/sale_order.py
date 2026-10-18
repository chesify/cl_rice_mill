from odoo import api, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.depends('user_id', 'company_id', 'order_line.product_id')
    def _compute_warehouse_id(self):
        super()._compute_warehouse_id()
        # rice mill products (bran, husk, the mill's rice) are only stored in the rice mill warehouse
        for order in self:
            warehouse = order.company_id.cl_rice_warehouse_id
            if warehouse and order.state in ('draft', 'sent') \
                    and any(order.order_line.product_id.mapped('cl_rice_product')):
                order.warehouse_id = warehouse
