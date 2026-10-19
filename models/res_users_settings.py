from odoo import fields, models


class ResUsersSettings(models.Model):
    _inherit = 'res.users.settings'

    cl_rice_dashboard_filters = fields.Json(string="Rice Mill Dashboard Filters")
