from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class ClRiceIntake(models.Model):
    _name = 'cl.rice.intake'
    _description = "Paddy Intake"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    intake_type = fields.Selection(
        [('farmer', "Farmer (season account)"), ('walkin', "Walk-in customer")],
        string="Type", required=True, default='farmer', tracking=True)
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index=True, tracking=True, check_company=True,
        domain="[('state', 'in', ('active', 'harvested', 'in_storage', 'released'))]")
    partner_id = fields.Many2one(
        'res.partner', string="Owner", required=True, index=True, tracking=True,
        compute='_compute_partner_id', store=True, readonly=False, precompute=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    state = fields.Selection(
        [('draft', "Draft"), ('done', "Received"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False, index=True)

    date = fields.Datetime(required=True, default=fields.Datetime.now, tracking=True)
    product_id = fields.Many2one(
        'product.product', string="Product", required=True, check_company=True,
        domain="[('cl_rice_product', '=', 'paddy')]",
        default=lambda self: self.env.ref('cl_rice_mill.product_paddy', raise_if_not_found=False))
    warehouse_id = fields.Many2one(
        'stock.warehouse', string="Warehouse", required=True, check_company=True,
        default=lambda self: self.env.company.cl_rice_warehouse_id)
    location_id = fields.Many2one(
        'stock.location', string="Partition", required=True, check_company=True,
        domain="[('usage', '=', 'internal'), ('warehouse_id', '=', warehouse_id)]",
        default=lambda self: self.env.company.cl_rice_paddy_location_id)
    bags = fields.Integer(string="Bags", tracking=True)
    gross_weight = fields.Float(string="Gross Weight (kg)", digits='Product Unit', tracking=True)
    tare_weight = fields.Float(
        string="Tare (kg)", digits='Product Unit', tracking=True,
        compute='_compute_tare_weight', store=True, readonly=False, precompute=True)
    net_weight = fields.Float(string="Net Weight (kg)", digits='Product Unit', compute='_compute_net_weight', store=True)
    moisture = fields.Float(string="Moisture (%)", digits=(5, 2), tracking=True)
    grade = fields.Selection([('a', "Grade A"), ('b', "Grade B"), ('c', "Grade C")], tracking=True)
    lot_id = fields.Many2one('stock.lot', string="Lot", readonly=True, copy=False)
    picking_id = fields.Many2one('stock.picking', string="Receipt", readonly=True, copy=False)
    received_by_id = fields.Many2one('res.users', string="Received By", default=lambda self: self.env.user, readonly=True)
    note = fields.Text()

    _weights_positive = models.Constraint(
        'CHECK(gross_weight >= 0 AND tare_weight >= 0 AND bags >= 0)', "Weights and bags cannot be negative.")
    _moisture_range = models.Constraint('CHECK(moisture >= 0 AND moisture <= 100)', "Moisture must be between 0 and 100%.")

    @api.depends('farmer_season_id')
    def _compute_partner_id(self):
        for intake in self:
            if intake.farmer_season_id:
                intake.partner_id = intake.farmer_season_id.partner_id

    @api.depends('bags', 'company_id')
    def _compute_tare_weight(self):
        for intake in self:
            intake.tare_weight = intake.bags * intake.company_id.cl_rice_bag_tare

    @api.depends('gross_weight', 'tare_weight')
    def _compute_net_weight(self):
        for intake in self:
            intake.net_weight = intake.gross_weight - intake.tare_weight

    @api.constrains('gross_weight', 'tare_weight')
    def _check_net_weight(self):
        for intake in self:
            if intake.tare_weight > intake.gross_weight:
                raise ValidationError(self.env._("The tare cannot be heavier than the gross weight."))

    @api.constrains('intake_type', 'farmer_season_id')
    def _check_farmer_season(self):
        for intake in self:
            if intake.intake_type == 'farmer' and not intake.farmer_season_id:
                raise ValidationError(self.env._("A farmer intake needs the farmer season."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.intake') or '/'
        intakes = super().create(vals_list)
        intakes.farmer_season_id._cl_check_open()
        return intakes

    def write(self, vals):
        frozen = {'farmer_season_id', 'partner_id', 'product_id', 'bags', 'gross_weight', 'tare_weight',
                  'warehouse_id', 'location_id'}
        if frozen & set(vals) and self.filtered(lambda i: i.state != 'draft'):
            raise UserError(self.env._("A received intake cannot be changed."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda i: i.state == 'done'):
            raise UserError(self.env._("A received intake cannot be deleted."))

    def _cl_get_lot(self):
        self.ensure_one()
        if self.farmer_season_id:
            return self.farmer_season_id._cl_get_lot(self.product_id)
        return self.env['stock.lot'].sudo().create({
            'name': f"{self.name}-{self.product_id.default_code or 'MP'}".replace('/', '-'),
            'product_id': self.product_id.id,
            'company_id': self.company_id.id,
            'cl_owner_id': self.partner_id.id,
        })

    def action_confirm(self):
        for intake in self:
            if intake.state != 'draft':
                raise UserError(self.env._("Intake %s is already processed.", intake.name))
            if intake.net_weight <= 0:
                raise UserError(self.env._("Enter the weights of intake %s.", intake.name))
            intake.farmer_season_id._cl_check_open()
            intake._cl_receive()

    def _cl_receive(self):
        self.ensure_one()
        lot = self._cl_get_lot()
        warehouse = self.warehouse_id
        picking = self.env['stock.picking']._cl_rice_done_transfer(
            warehouse.in_type_id,
            self.partner_id.property_stock_supplier,
            self.location_id,
            self.partner_id,
            [{'product': self.product_id, 'qty': self.net_weight, 'lot': lot, 'bags': self.bags}],
            origin=self.name,
        )
        self.write({'state': 'done', 'lot_id': lot.id, 'picking_id': picking.id})
        self.farmer_season_id._cl_advance_state(('active', 'harvested'), 'in_storage')
        self.message_post(body=self.env._(
            "%(kg)s kg in %(bags)s bags received into %(store)s, lot %(lot)s.",
            kg=self.net_weight, bags=self.bags, store=self.location_id.display_name, lot=lot.name))
        self.partner_id._cl_rice_notify('intake_done', self)

    def action_cancel(self):
        if self.filtered(lambda i: i.state == 'done'):
            raise UserError(self.env._("A received intake can only be reversed by a manager, with a reason."))
        self.state = 'cancelled'

    def action_open_reverse(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Reverse Intake"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_action': 'reverse'},
        }

    def _cl_reverse(self, reason, amount=None, date=None):
        """Send the received paddy back out (a weighing mistake), if it is still in the store."""
        self.ensure_one()
        self._cl_check_manager()
        if self.state != 'done':
            raise UserError(self.env._("Only received intakes can be reversed."))
        self.farmer_season_id._cl_check_open()
        quants = self.env['stock.quant']._cl_owned_quants(
            self.product_id, self.lot_id, self.partner_id, self.warehouse_id.lot_stock_id)
        self.env['stock.picking']._cl_rice_done_transfer(
            self.warehouse_id.out_type_id,
            self.location_id,
            self.partner_id.property_stock_supplier,
            self.partner_id,
            [{'product': self.product_id, 'qty': self.net_weight, 'lot': self.lot_id, 'bags': self.bags, 'quants': quants}],
            origin=self.env._("Reversal of %s", self.name),
        )
        self.state = 'cancelled'
        self.message_post(body=self.env._(
            "Intake reversed by %(user)s. Reason: %(reason)s", user=self.env.user.name, reason=reason))

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_reason_actions(self):
        return {'reverse': self._cl_reverse}
