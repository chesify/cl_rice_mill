from odoo import models


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _get_default_warehouse_id(self):
        # rice mill staff work in the rice mill warehouse, not in the company's first warehouse
        warehouse = self.env.company.cl_rice_warehouse_id
        if warehouse and self.has_group('cl_rice_mill.cl_rice_mill_group_user'):
            return warehouse
        return super()._get_default_warehouse_id()
