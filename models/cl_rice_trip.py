from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class ClRiceTrip(models.Model):
    _name = 'cl.rice.trip'
    _description = "Staff Field Trip"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    employee_id = fields.Many2one(
        'hr.employee', string="Staff", required=True, check_company=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    user_id = fields.Many2one('res.users', string="Recorded By", default=lambda self: self.env.user, readonly=True,
                              index=True)
    vehicle_id = fields.Many2one(
        'fleet.vehicle', string="Vehicle", required=True, check_company=True, tracking=True,
        domain="[('cl_machine_category', 'in', ('motorcycle', 'car', 'truck'))]")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    purpose = fields.Char(required=True)
    farmer_season_id = fields.Many2one('cl.rice.farmer.season', string="Farmer Season", check_company=True,
                                       index='btree_not_null')
    village = fields.Char()
    odometer_start = fields.Float(string="Odometer Start", digits=(16, 1))
    odometer_end = fields.Float(string="Odometer End", digits=(16, 1))
    km = fields.Float(string="Distance (km)", compute='_compute_km', store=True, digits=(16, 1))
    fuel_litres = fields.Float(string="Fuel Used (L)", digits=(16, 2))
    fuel_cost = fields.Monetary(compute='_compute_fuel_cost', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection(
        [('draft', "Draft"), ('posted', "Posted"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False, index=True)
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    source = fields.Selection(
        [('office', "Office"), ('field', "Field App")], default='office', readonly=True, copy=False)
    cl_client_uuid = fields.Char(string="Field App ID", readonly=True, copy=False)

    _cl_client_uuid_uniq = models.UniqueIndex(
        '(cl_client_uuid) WHERE cl_client_uuid IS NOT NULL', "This field record was already received.")
    _figures_positive = models.Constraint(
        'CHECK(odometer_start >= 0 AND odometer_end >= 0 AND fuel_litres >= 0)', "Figures cannot be negative.")

    @api.depends('odometer_start', 'odometer_end')
    def _compute_km(self):
        for trip in self:
            trip.km = max(trip.odometer_end - trip.odometer_start, 0.0) if trip.odometer_end else 0.0

    @api.depends('fuel_litres', 'company_id.cl_rice_fuel_price')
    def _compute_fuel_cost(self):
        for trip in self:
            trip.fuel_cost = trip.currency_id.round(trip.fuel_litres * trip.company_id.cl_rice_fuel_price)

    @api.constrains('odometer_start', 'odometer_end')
    def _check_odometer(self):
        for trip in self:
            if trip.odometer_end and trip.odometer_end < trip.odometer_start:
                raise ValidationError(self.env._("The odometer at the end cannot be lower than at the start."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.trip') or '/'
        return super().create(vals_list)

    def write(self, vals):
        if self.filtered(lambda t: t.state == 'posted') and set(vals) - {'state', 'move_id', 'purpose', 'village'}:
            raise UserError(self.env._("A posted trip cannot be changed."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda t: t.state == 'posted'):
            raise UserError(self.env._("A posted trip cannot be deleted."))

    def action_post(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only the office (Rice Mill Accountant) can post trips."))
        for trip in self:
            if trip.state != 'draft':
                raise UserError(self.env._("Trip %s is already processed.", trip.name))
            trip._cl_post()

    def _cl_distribution(self):
        self.ensure_one()
        accounts = self.vehicle_id._cl_get_analytic_account()
        if self.farmer_season_id:
            return self.farmer_season_id._cl_analytic_distribution(accounts)
        accounts |= self.env['cl.rice.season']._cl_current(self.company_id).project_id.account_id
        return {",".join(str(i) for i in accounts.ids): 100.0}

    def _cl_post(self):
        self.ensure_one()
        company = self.company_id
        values = {'state': 'posted'}
        if self.fuel_cost:
            if not company.cl_rice_transport_account_id or not company.cl_rice_fuel_clearing_account_id:
                raise UserError(self.env._("Run the Rice Mill setup: the transport and fuel accounts are missing."))
            label = self.env._("Trip %(ref)s - %(staff)s - %(purpose)s", ref=self.name, staff=self.employee_id.name,
                               purpose=self.purpose)
            move = self.env['account.move'].sudo().create({
                'move_type': 'entry',
                'journal_id': company.cl_rice_cost_journal_id.id,
                'date': self.date,
                'ref': label,
                'company_id': company.id,
                'line_ids': [
                    (0, 0, {'name': label, 'account_id': company.cl_rice_transport_account_id.id,
                            'debit': self.fuel_cost, 'analytic_distribution': self._cl_distribution(),
                            'vehicle_id': self.vehicle_id.id}),
                    (0, 0, {'name': label, 'account_id': company.cl_rice_fuel_clearing_account_id.id,
                            'credit': self.fuel_cost}),
                ],
            })
            move.action_post()
            values['move_id'] = move.id
        if self.odometer_end:
            self.env['fleet.vehicle.odometer'].sudo().create({
                'vehicle_id': self.vehicle_id.id, 'value': self.odometer_end, 'date': self.date})
        self.write(values)

    def action_cancel(self):
        for trip in self:
            if trip.move_id.state == 'posted':
                trip.move_id._reverse_moves([{'ref': self.env._("Cancellation of %s", trip.name)}], cancel=True)
        self.state = 'cancelled'
