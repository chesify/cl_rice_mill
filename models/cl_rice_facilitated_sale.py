from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ClRiceFacilitatedSale(models.Model):
    _name = 'cl.rice.facilitated.sale'
    _description = "Facilitated Sale"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", required=True, index=True, tracking=True,
        check_company=True, domain="[('state', 'in', ('harvested', 'in_storage', 'released'))]")
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer", index=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    buyer_id = fields.Many2one('res.partner', string="Buyer", required=True, tracking=True)
    product_id = fields.Many2one(
        'product.product', string="Product", required=True,
        domain="[('cl_rice_product', 'in', ('paddy', 'rice'))]")
    lot_id = fields.Many2one(
        'stock.lot', string="Lot", compute='_compute_lot_id', store=True, readonly=False,
        domain="[('cl_farmer_season_id', '=', farmer_season_id), ('product_id', '=', product_id)]")
    bags = fields.Float(digits=(16, 1), tracking=True)
    quantity = fields.Float(string="Quantity (kg)", digits='Product Unit', required=True, tracking=True)
    price_unit = fields.Monetary(
        string="Price per kg", tracking=True,
        help="Price agreed between the farmer and the buyer. For reference only: the mill earns nothing on the sale.")
    amount = fields.Monetary(string="Sale Value", compute='_compute_amount', store=True)
    payment_route = fields.Selection(
        [('luvanda', "Buyer pays the mill"), ('farmer', "Buyer pays the farmer directly")],
        string="Payment Route", required=True, default='farmer', tracking=True)
    vehicle_plate = fields.Char(string="Vehicle Plate", tracking=True)
    driver_name = fields.Char(string="Driver", tracking=True)
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('cleared', "Cleared for Release"),
            ('released', "Released"),
            ('done', "Done"),
            ('cancelled', "Cancelled"),
        ],
        default='draft', required=True, tracking=True, copy=False, index=True)
    release_ids = fields.One2many('cl.rice.stock.release', 'sale_id', string="Releases")
    payment_ids = fields.One2many('cl.rice.farmer.payment', 'sale_id', string="Buyer Payments")
    released_qty = fields.Float(string="Released (kg)", compute='_compute_quantities', digits='Product Unit')
    remaining_qty = fields.Float(string="Still to Release (kg)", compute='_compute_quantities', digits='Product Unit')
    available_qty = fields.Float(
        string="Farmer Holds (kg)", compute='_compute_quantities', digits='Product Unit',
        help="Stock of this lot the farmer still owns in the warehouse.")
    buyer_paid = fields.Monetary(string="Paid by the Buyer to the Mill", compute='_compute_buyer_paid')
    note = fields.Text()

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', "The quantity must be positive.")
    _price_positive = models.Constraint('CHECK(price_unit >= 0 AND bags >= 0)', "Price and bags cannot be negative.")

    @api.depends('farmer_season_id', 'product_id')
    def _compute_lot_id(self):
        for sale in self:
            if sale.farmer_season_id and sale.product_id:
                sale.lot_id = sale.farmer_season_id._cl_find_lot(sale.product_id)

    @api.depends('quantity', 'price_unit')
    def _compute_amount(self):
        for sale in self:
            sale.amount = sale.currency_id.round(sale.quantity * sale.price_unit)

    @api.depends('release_ids.state', 'release_ids.quantity', 'quantity', 'lot_id')
    def _compute_quantities(self):
        Quant = self.env['stock.quant']
        for sale in self:
            sale.released_qty = sum(sale.release_ids.filtered(lambda r: r.state == 'released').mapped('quantity'))
            sale.remaining_qty = max(sale.quantity - sale.released_qty, 0.0)
            if sale.lot_id:
                sale.available_qty = Quant._cl_available_qty(
                    sale.product_id, sale.lot_id, sale.partner_id, sale.company_id._cl_rice_store_locations())
            else:
                sale.available_qty = 0.0

    @api.depends('payment_ids.state', 'payment_ids.amount')
    def _compute_buyer_paid(self):
        for sale in self:
            # sudo: the warehouse sees how much the buyer paid without access to the payments themselves
            sale.buyer_paid = sum(sale.sudo().payment_ids.filtered(lambda p: p.state == 'posted').mapped('amount'))

    @api.constrains('farmer_season_id', 'lot_id', 'product_id')
    def _check_lot(self):
        for sale in self:
            if sale.lot_id and (sale.lot_id.cl_farmer_season_id != sale.farmer_season_id
                                or sale.lot_id.product_id != sale.product_id):
                raise ValidationError(self.env._(
                    "Lot %(lot)s is not the farmer's %(product)s of this season.",
                    lot=sale.lot_id.name, product=sale.product_id.name))

    @api.model_create_multi
    def create(self, vals_list):
        FarmerSeason = self.env['cl.rice.farmer.season']
        for vals in vals_list:
            company = FarmerSeason.browse(vals.get('farmer_season_id')).company_id or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code(
                    'cl.rice.facilitated.sale') or '/'
        sales = super().create(vals_list)
        sales.farmer_season_id._cl_check_open()
        return sales

    def write(self, vals):
        frozen = {'farmer_season_id', 'product_id', 'lot_id', 'payment_route'}
        if frozen & set(vals) and self.filtered(lambda s: s.release_ids.filtered(lambda r: r.state != 'cancelled')):
            raise UserError(self.env._("The farmer, product, lot and payment route cannot change once stock is cleared."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda s: s.state not in ('draft', 'cancelled')):
            raise UserError(self.env._("Only draft sales can be deleted."))

    def _cl_update_state(self):
        for sale in self.filtered(lambda s: s.state not in ('done', 'cancelled')):
            releases = sale.release_ids
            released = sum(releases.filtered(lambda r: r.state == 'released').mapped('quantity'))
            if released and sale.product_id.uom_id.compare(released, sale.quantity) >= 0:
                state = 'done'
            elif releases.filtered(lambda r: r.state == 'cleared'):
                state = 'cleared'
            elif released:
                state = 'released'
            else:
                state = 'draft'
            if sale.state != state:
                sale.state = state

    def action_new_release(self):
        """Release Stock: prepare the next (partial) release of this sale."""
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(self.env._("This sale is closed."))
        self.farmer_season_id._cl_check_open()
        pending = sum(self.release_ids.filtered(lambda r: r.state in ('draft', 'cleared')).mapped('quantity'))
        quantity = max(min(self.remaining_qty - pending, self.available_qty), 0.0)
        bags = self.bags * quantity / self.quantity if self.quantity else 0.0
        return {
            'name': self.env._("Release Stock"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.stock.release',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_sale_id': self.id,
                'default_quantity': quantity,
                'default_bags': round(bags, 1),
                'default_vehicle_plate': self.vehicle_plate,
                'default_driver_name': self.driver_name,
            },
        }

    def action_receive_buyer_payment(self):
        self.ensure_one()
        if self.payment_route != 'luvanda':
            raise UserError(self.env._("On this sale the buyer pays the farmer directly."))
        return {
            'name': self.env._("Receive Payment"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.farmer.payment',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_farmer_season_id': self.farmer_season_id.id,
                'default_payment_type': 'buyer',
                'default_sale_id': self.id,
                'default_amount': max(self.amount - self.buyer_paid, 0.0),
            },
        }

    def action_done(self):
        for sale in self:
            if sale.release_ids.filtered(lambda r: r.state == 'cleared'):
                raise UserError(self.env._("Release or cancel the cleared releases first."))
        self.state = 'done'

    def action_cancel(self):
        for sale in self:
            if sale.release_ids.filtered(lambda r: r.state == 'released'):
                raise UserError(self.env._("Stock was already released on %s: close it instead.", sale.name))
        self.release_ids.filtered(lambda r: r.state in ('draft', 'cleared')).write({'state': 'cancelled'})
        self.state = 'cancelled'

    def action_view_releases(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_stock_release_action')
        action['domain'] = [('sale_id', '=', self.id)]
        action['context'] = {'default_sale_id': self.id}
        return action
