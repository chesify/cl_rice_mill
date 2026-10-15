from odoo import fields, models

MACHINE_CATEGORIES = [
    ('tractor', "Tractor"),
    ('harvester', "Harvester"),
    ('mill', "Mill Machine"),
    ('motorcycle', "Motorcycle"),
    ('car', "Car"),
    ('truck', "Truck"),
]


class FleetVehicle(models.Model):
    _inherit = 'fleet.vehicle'

    cl_machine_category = fields.Selection(MACHINE_CATEGORIES, string="Machine Category", tracking=True)
    cl_analytic_account_id = fields.Many2one(
        'account.analytic.account', string="Machine Analytic Account", copy=False, index='btree_not_null')

    def _cl_get_analytic_account(self):
        """Return the machine analytic account, creating it on first use."""
        self.ensure_one()
        if not self.cl_analytic_account_id:
            self.sudo().cl_analytic_account_id = self.env['account.analytic.account'].sudo().create({
                'name': self.display_name,
                'code': self.license_plate or False,
                'plan_id': self.env.ref('cl_rice_mill.analytic_plan_machines').id,
                'company_id': self.company_id.id,
            })
        return self.cl_analytic_account_id
