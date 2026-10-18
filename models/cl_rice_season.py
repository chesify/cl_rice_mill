from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .product_template import LOT_SUFFIX
from .project_task_type import TASK_STAGES


class ClRiceSeason(models.Model):
    _name = 'cl.rice.season'
    _description = "Season"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'

    name = fields.Char(required=True, tracking=True, help="e.g. Season 2026/27")
    code = fields.Char(
        required=True, tracking=True, size=8,
        help="Short code used in lot numbers, e.g. 2627.")
    date_start = fields.Date(string="Start Date", required=True, tracking=True)
    date_end = fields.Date(string="End Date", required=True, tracking=True)
    state = fields.Selection(
        [
            ('planning', "Planning"),
            ('active', "Active"),
            ('harvest', "Harvest"),
            ('closing', "Closing"),
            ('closed', "Closed"),
        ],
        default='planning', required=True, tracking=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    reference_price = fields.Monetary(
        string="Reference Paddy Price (per kg)", tracking=True,
        help="Used to value farmers' stock (collateral) and their credit limit.")
    rice_reference_price = fields.Monetary(
        string="Reference Rice Price (per kg)", tracking=True,
        help="Used to value milled rice held as collateral.")
    project_id = fields.Many2one('project.project', string="Project", readonly=True, copy=False)
    project_account_id = fields.Many2one(
        'account.analytic.account', string="Project Analytic Account", related='project_id.account_id',
        help="Every entry of the season carries it, so the project shows the season's income and costs.")
    analytic_plan_id = fields.Many2one('account.analytic.plan', string="Analytic Plan", readonly=True, copy=False)
    farmer_season_ids = fields.One2many('cl.rice.farmer.season', 'season_id', string="Farmer Seasons")
    active = fields.Boolean(default=True)

    # KPIs
    farmer_count = fields.Integer(string="Farmers", compute='_compute_kpis')
    acreage = fields.Float(string="Acreage Planted", compute='_compute_kpis')
    credit_limit_total = fields.Monetary(string="Total Credit Limit", compute='_compute_kpis')

    _code_company_uniq = models.Constraint(
        'UNIQUE(code, company_id)', "The season code must be unique per company.")
    _dates_check = models.Constraint(
        'CHECK(date_end >= date_start)', "The season cannot end before it starts.")

    @api.depends('farmer_season_ids.acreage', 'farmer_season_ids.credit_limit', 'farmer_season_ids.state')
    def _compute_kpis(self):
        for season in self:
            fss = season.farmer_season_ids
            season.farmer_count = len(fss)
            season.acreage = sum(fss.mapped('acreage'))
            season.credit_limit_total = sum(fss.mapped('credit_limit'))

    @api.constrains('code')
    def _check_code(self):
        for season in self:
            if not season.code.isalnum():
                raise ValidationError(self.env._("The season code may only contain letters and digits."))

    @api.model_create_multi
    def create(self, vals_list):
        seasons = super().create(vals_list)
        seasons._cl_create_project_and_plan()
        return seasons

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals:
            for season in self:
                season.analytic_plan_id.sudo().name = season.name
                season.project_id.sudo().name = season.name
                season.project_id.account_id.sudo().name = season.name
        return res

    def _cl_project_account_vals(self):
        self.ensure_one()
        return {
            'name': self.name,
            'code': f"PRJ-{self.code}",
            'plan_id': self.env.ref('analytic.analytic_plan_projects').id,
            'company_id': self.company_id.id,
        }

    def _cl_ensure_project_account(self):
        """Give the season project its analytic account (reusing the one already set on the project)."""
        AnalyticAccount = self.env['account.analytic.account'].sudo()
        for season in self.filtered('project_id'):
            account = season.project_id.account_id
            if account:
                account.write(season._cl_project_account_vals())
            else:
                season.project_id.sudo().account_id = AnalyticAccount.create(season._cl_project_account_vals())

    @api.model
    def _cl_current(self, company=None):
        """The running season of the company (the latest one started and not closed)."""
        company = company or self.env.company
        domain = [('company_id', '=', company.id)]
        return (self.search(domain + [('state', 'in', ('active', 'harvest', 'closing'))], order='date_start desc', limit=1)
                or self.search(domain + [('state', '=', 'planning')], order='date_start desc', limit=1))

    def _cl_create_project_and_plan(self):
        root_plan = self.env.ref('cl_rice_mill.analytic_plan_farmer_seasons')
        for season in self:
            season.analytic_plan_id = self.env['account.analytic.plan'].sudo().create({
                'name': season.name,
                'parent_id': root_plan.id,
            })
            season.project_id = self.env['project.project'].sudo().create({
                'name': season.name,
                'company_id': season.company_id.id,
            })
        self._cl_ensure_project_account()
        self._cl_ensure_task_stages()
        self._cl_detach_default_stages()

    def _cl_ensure_task_stages(self):
        """The season project gets the farmer stages (Registered ... Settled), created once."""
        Stage = self.env['project.task.type'].sudo()
        for season in self.filtered('project_id'):
            project = season.project_id
            existing = Stage.search([('project_ids', 'in', project.ids), ('cl_rice_stage', '!=', False)])
            for sequence, (key, name) in enumerate(TASK_STAGES, start=1):
                if key in existing.mapped('cl_rice_stage'):
                    continue
                Stage.create({
                    'name': name,
                    'sequence': sequence,
                    'cl_rice_stage': key,
                    'project_ids': [(4, project.id)],
                    'fold': key == 'settled',
                })

    def _cl_detach_default_stages(self):
        """Only the farmer stages stay on the season project (generic stages still holding its tasks stay)."""
        Task = self.env['project.task'].sudo().with_context(active_test=False)
        for season in self.filtered('project_id'):
            project = season.project_id
            for stage in project.sudo().type_ids.filtered(lambda s: not s.cl_rice_stage):
                if not Task.search_count([('project_id', '=', project.id), ('stage_id', '=', stage.id)], limit=1):
                    stage.project_ids = [(3, project.id)]

    def _cl_task_stage(self, key):
        self.ensure_one()
        return self.env['project.task.type'].sudo().search(
            [('project_ids', 'in', self.project_id.ids), ('cl_rice_stage', '=', key)], limit=1)

    def action_activate(self):
        self._cl_set_state('planning', 'active')

    def action_harvest(self):
        self._cl_set_state('active', 'harvest')

    def action_closing(self):
        self._cl_set_state('harvest', 'closing')

    def action_close(self):
        for season in self:
            open_fs = season.farmer_season_ids.filtered(lambda fs: fs.state not in ('settled', 'closed'))
            if open_fs:
                raise UserError(self.env._(
                    "The season %(season)s still has %(count)s farmer seasons that are not settled.",
                    season=season.name, count=len(open_fs)))
        self._cl_set_state('closing', 'closed')
        self.farmer_season_ids.filtered(lambda fs: fs.state == 'settled').write({'state': 'closed'})

    def _cl_set_state(self, from_state, to_state):
        for season in self:
            if season.state != from_state:
                raise UserError(self.env._(
                    "Season %(season)s must be in state %(state)s for this action.",
                    season=season.name,
                    state=dict(self._fields['state']._description_selection(self.env))[from_state]))
        self.state = to_state

    def action_view_farmer_seasons(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_farmer_season_action')
        action['domain'] = [('season_id', '=', self.id)]
        action['context'] = {'default_season_id': self.id}
        return action

    @api.model
    def _cl_get_mill_lot(self, product, season, company):
        """Lot of the mill's own ``product`` for ``season`` (by-products kept, bought crop)."""
        Lot = self.env['stock.lot'].sudo()
        domain = [
            ('product_id', '=', product.id),
            ('company_id', '=', company.id),
            ('cl_owner_id', '=', False),
            ('cl_farmer_season_id', '=', False),
            ('cl_season_id', '=', season.id),
        ]
        lot = Lot.search(domain, limit=1)
        if not lot:
            suffix = LOT_SUFFIX.get(product.cl_rice_product) or product.default_code or 'X'
            lot = Lot.create({
                'name': f"MILL-{season.code or 'GEN'}-{suffix}",
                'product_id': product.id,
                'company_id': company.id,
                'cl_season_id': season.id,
            })
        return lot

    def action_open_project(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'project.project',
            'res_id': self.project_id.id,
            'view_mode': 'form',
        }
