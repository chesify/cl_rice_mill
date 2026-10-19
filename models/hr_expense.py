from odoo import api, fields, models


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    cl_vehicle_id = fields.Many2one(
        'fleet.vehicle', string="Machine / Vehicle", check_company=True, index='btree_not_null',
        help="Machine or vehicle this cost is for: it reaches its profitability.")
    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", check_company=True, index='btree_not_null')

    @api.depends('cl_vehicle_id', 'cl_farmer_season_id')
    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        for expense in self.filtered(lambda e: e.company_id.cl_rice_warehouse_id):
            expense.analytic_distribution = expense._cl_rice_distribution(expense.analytic_distribution)

    def _cl_rice_distribution(self, distribution):
        """Add the machine, the farmer season and the season project, each at 100% of its own plan."""
        self.ensure_one()
        distribution = dict(distribution or {})
        accounts = self.env['account.analytic.account']
        if self.cl_vehicle_id:
            accounts |= self.cl_vehicle_id._cl_get_analytic_account()
        season = self.cl_farmer_season_id.season_id or self.env['cl.rice.season']._cl_current(self.company_id)
        if self.cl_farmer_season_id:
            accounts |= self.cl_farmer_season_id.analytic_account_id
        accounts |= season.project_id.account_id
        present = {account_id for key in distribution for account_id in key.split(',')}
        for account in accounts:
            if str(account.id) not in present:
                distribution[str(account.id)] = 100.0
        return distribution

    def _create_move(self):
        res = super()._create_move()
        # Odoo leaves the entry of a company-paid expense in draft: the expense shows paid while the
        # cost is missing from the profit and loss and from the cash book. Post it.
        moves = self.filtered(
            lambda e: e.payment_mode == 'company_account' and e.company_id.cl_rice_post_expenses
        ).sudo().account_move_id.filtered(lambda m: m.state == 'draft')
        for expense in self.filtered('cl_vehicle_id'):
            # the machine's profit reads the vehicle of each cost line
            expense.sudo().account_move_id.line_ids.filtered(
                lambda l: l.expense_id == expense and l.account_id.account_type.startswith('expense')
            ).vehicle_id = expense.cl_vehicle_id
        moves.action_post()
        return res
