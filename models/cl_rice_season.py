from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .product_template import LOT_SUFFIX


class ClRiceSeason(models.Model):
    _name = 'cl.rice.season'
    _description = "Season (Msimu)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'

    name = fields.Char(required=True, tracking=True, help="e.g. Msimu 2026/27")
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
        return res

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
