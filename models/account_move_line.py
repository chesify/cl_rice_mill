from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index='btree_not_null',
        help="Farmer season this journal item is owed by or due to. Drives the farmer statement and the settlement.")

    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        # vendor bills of a rice mill: costs go to the running season's project by default
        bill_lines = self.filtered(
            lambda l: l.display_type == 'product'
            and l.move_id.move_type in ('in_invoice', 'in_refund')
            and l.account_id.internal_group == 'expense'
            and l.company_id.cl_rice_warehouse_id
        )
        Season = self.env['cl.rice.season'].sudo()
        projects = {}
        for line in bill_lines:
            company = line.company_id
            if company not in projects:
                projects[company] = Season._cl_current(company).project_id.account_id
            account = projects[company]
            if not account:
                continue
            distribution = dict(line.analytic_distribution or {})
            if any(str(account.id) in key.split(',') for key in distribution):
                continue
            distribution[str(account.id)] = 100.0
            line.analytic_distribution = distribution
