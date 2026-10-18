from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ClRiceWalkinCollection(models.Model):
    """A walk-in customer collects the rice milled for him (no farmer account, no release control:
    only his unpaid storage or milling invoices block the collection)."""
    _name = 'cl.rice.walkin.collection'
    _description = "Walk-in Collection"
    _inherit = ['mail.thread']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    partner_id = fields.Many2one(
        'res.partner', string="Customer", required=True, index=True, tracking=True, check_company=True)
    lot_id = fields.Many2one(
        'stock.lot', string="Lot", required=True, tracking=True, check_company=True,
        domain="[('cl_owner_id', '=', partner_id), ('cl_farmer_season_id', '=', False)]")
    product_id = fields.Many2one(related='lot_id.product_id', store=True)
    available_qty = fields.Float(string="In Store (kg)", compute='_compute_available_qty', digits='Product Unit')
    quantity = fields.Float(string="Quantity (kg)", digits='Product Unit', required=True, tracking=True)
    bags = fields.Float(
        digits=(16, 1), tracking=True, compute='_compute_bags', store=True, readonly=False,
        help="Defaults to the lot's bags in proportion to the kg collected.")
    vehicle_plate = fields.Char(string="Vehicle Plate", tracking=True)
    driver_name = fields.Char(string="Driver", tracking=True)
    state = fields.Selection(
        [('draft', "Draft"), ('released', "Collected"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False, index=True)
    unpaid_amount = fields.Monetary(
        string="Unpaid Invoices", compute='_compute_unpaid_amount',
        help="Storage and milling invoices of the customer still to pay: they block the collection.")
    currency_id = fields.Many2one(related='company_id.currency_id')
    picking_id = fields.Many2one('stock.picking', string="Transfer", readonly=True, copy=False)
    released_uid = fields.Many2one('res.users', string="Released By", readonly=True, copy=False)
    released_date = fields.Datetime(string="Released On", readonly=True, copy=False)

    _quantity_positive = models.Constraint('CHECK(quantity > 0 AND bags >= 0)', "The quantity must be positive.")

    @api.depends('lot_id', 'partner_id')
    def _compute_available_qty(self):
        Quant = self.env['stock.quant']
        for collection in self:
            if collection.lot_id and collection.partner_id:
                collection.available_qty = Quant._cl_available_qty(
                    collection.product_id, collection.lot_id, collection.partner_id,
                    collection.company_id._cl_rice_store_locations())
            else:
                collection.available_qty = 0.0

    @api.depends('quantity', 'lot_id')
    def _compute_bags(self):
        for collection in self:
            if collection.state == 'draft' and collection.lot_id:
                collection.bags = collection.lot_id._cl_bags_for_kg(collection.quantity)

    @api.depends('partner_id', 'company_id')
    def _compute_unpaid_amount(self):
        for collection in self:
            collection.unpaid_amount = sum(collection._cl_unpaid_invoices().mapped('amount_residual'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code(
                    'cl.rice.walkin.collection') or '/'
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda c: c.state == 'released'):
            raise UserError(self.env._("A collection that left the warehouse cannot be deleted."))

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.lot_id.cl_owner_id != self.partner_id:
            self.lot_id = self.env['stock.lot'].search([
                ('cl_owner_id', '=', self.partner_id.id), ('cl_farmer_season_id', '=', False),
                ('product_id.cl_rice_product', 'in', ('rice', 'broken'))], limit=1) if self.partner_id else False

    def _cl_unpaid_invoices(self):
        """Storage and milling invoices of the customer not paid yet."""
        self.ensure_one()
        company = self.company_id
        journals = company.cl_rice_charge_journal_id | company.cl_rice_walkin_journal_id
        if not self.partner_id or not journals:
            return self.env['account.move']
        return self.env['account.move'].sudo().search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('commercial_partner_id', '=', self.partner_id.commercial_partner_id.id),
            ('journal_id', 'in', journals.ids),
            ('payment_state', 'not in', ('paid', 'in_payment', 'reversed')),
            ('amount_residual', '>', 0),
        ])

    def action_release(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_warehouse'):
            raise AccessError(self.env._("Only the warehouse can release stock."))
        for collection in self:
            if collection.state != 'draft':
                raise UserError(self.env._("Collection %s is already processed.", collection.name))
            if collection.lot_id.cl_farmer_season_id:
                raise UserError(self.env._("Lot %s belongs to a farmer season: use a stock release.", collection.lot_id.name))
            unpaid = collection._cl_unpaid_invoices()
            if unpaid:
                raise UserError(self.env._(
                    "Collection blocked: %(customer)s must first pay %(amount)s of storage and milling invoices (%(refs)s).",
                    customer=collection.partner_id.name,
                    amount=collection.currency_id.format(sum(unpaid.mapped('amount_residual'))),
                    refs=", ".join(unpaid.mapped('name'))))
            if collection.product_id.uom_id.compare(collection.quantity, collection.available_qty) > 0:
                raise UserError(self.env._(
                    "Only %(available)s kg is in store in lot %(lot)s.",
                    available=round(collection.available_qty, 2), lot=collection.lot_id.name))
            collection._cl_move_stock()

    def _cl_move_stock(self):
        self.ensure_one()
        company = self.company_id
        quants = self.env['stock.quant']._cl_owned_quants(
            self.product_id, self.lot_id, self.partner_id, company._cl_rice_store_locations())
        picking = self.env['stock.picking']._cl_rice_done_transfer(
            company.cl_rice_release_type_id,
            quants[:1].location_id or company._cl_rice_location_for(self.product_id),
            self.partner_id.property_stock_customer,
            self.partner_id,
            [{'product': self.product_id, 'qty': self.quantity, 'lot': self.lot_id, 'bags': self.bags,
              'quants': quants}],
            origin=self.name,
        )
        self.write({
            'state': 'released',
            'picking_id': picking.id,
            'released_uid': self.env.uid,
            'released_date': fields.Datetime.now(),
        })
        self.message_post(body=self.env._(
            "%(qty)s kg (%(bags)s bags) collected by %(customer)s, vehicle %(plate)s.",
            qty=self.quantity, bags=self.bags, customer=self.partner_id.name, plate=self.vehicle_plate or '-'))

    def action_cancel(self):
        if self.filtered(lambda c: c.state == 'released'):
            raise UserError(self.env._("Stock that left the warehouse cannot be cancelled."))
        self.state = 'cancelled'

    def action_print_gate_pass(self):
        self.ensure_one()
        if self.state != 'released':
            raise UserError(self.env._("The gate pass is printed once the stock is released."))
        return self.env.ref('cl_rice_mill.action_report_walkin_gate_pass').report_action(self)
