from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ClRiceOwnershipTransfer(models.Model):
    _name = 'cl.rice.ownership.transfer'
    _description = "Crop Purchase from Farmer"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", required=True, index=True, check_company=True,
        domain="[('state', 'in', ('harvested', 'in_storage', 'sold'))]")
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer", index=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    lot_id = fields.Many2one(
        'stock.lot', string="Lot", required=True, check_company=True,
        domain="[('cl_farmer_season_id', '=', farmer_season_id)]")
    product_id = fields.Many2one(related='lot_id.product_id', store=True)
    quantity = fields.Float(string="Quantity (kg)", digits='Product Unit', required=True, tracking=True)
    bags = fields.Float(digits=(16, 1))
    price_unit = fields.Monetary(string="Price per kg", required=True, tracking=True)
    amount = fields.Monetary(compute='_compute_amount', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    origin = fields.Char()
    state = fields.Selection(
        [('draft', "Draft"), ('done', "Done"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False)
    out_picking_id = fields.Many2one('stock.picking', string="Released from Farmer", readonly=True, copy=False)
    in_picking_id = fields.Many2one('stock.picking', string="Received by the Mill", readonly=True, copy=False)
    bill_id = fields.Many2one('account.move', string="Farmer Bill", readonly=True, copy=False)

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', "The quantity must be positive.")
    _price_positive = models.Constraint('CHECK(price_unit >= 0)', "The price cannot be negative.")

    @api.depends('quantity', 'price_unit')
    def _compute_amount(self):
        for transfer in self:
            transfer.amount = transfer.currency_id.round(transfer.quantity * transfer.price_unit)

    @api.model_create_multi
    def create(self, vals_list):
        FarmerSeason = self.env['cl.rice.farmer.season']
        for vals in vals_list:
            company = FarmerSeason.browse(vals.get('farmer_season_id')).company_id or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code(
                    'cl.rice.ownership.transfer') or '/'
        return super().create(vals_list)

    def action_done(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only a Rice Mill Accountant can buy crop from farmers."))
        for transfer in self:
            if transfer.state != 'draft':
                raise UserError(self.env._("Crop purchase %s is already processed.", transfer.name))
            transfer.farmer_season_id._cl_check_open()
            transfer._cl_transfer_stock()
            transfer._cl_create_bill()
            transfer.state = 'done'
            transfer.farmer_season_id._cl_advance_state(('harvested', 'in_storage'), 'sold')

    def _cl_transfer_stock(self):
        self.ensure_one()
        company = self.company_id
        farmer = self.partner_id
        quants = self.env['stock.quant']._cl_owned_quants(
            self.product_id, self.lot_id, farmer, company._cl_rice_store_locations())
        if not quants:
            raise UserError(self.env._("Farmer %s holds no stock in this lot.", farmer.name))
        store = quants[0].location_id
        warehouse = store.warehouse_id
        Picking = self.env['stock.picking']
        vendor_location = farmer.property_stock_supplier
        out_picking = Picking._cl_rice_done_transfer(
            warehouse.out_type_id, store, vendor_location, farmer,
            [{'product': self.product_id, 'qty': self.quantity, 'lot': self.lot_id, 'bags': self.bags, 'quants': quants}],
            origin=self.name,
        )
        in_picking = Picking._cl_rice_done_transfer(
            warehouse.in_type_id, vendor_location, store, self.env['res.partner'],
            [{'product': self.product_id, 'qty': self.quantity, 'lot': self.lot_id, 'bags': self.bags,
              'value': self.amount}],
            origin=self.name, partner=farmer,
        )
        self.write({'out_picking_id': out_picking.id, 'in_picking_id': in_picking.id})

    def _cl_create_bill(self):
        self.ensure_one()
        if not self.amount:
            return
        fs = self.farmer_season_id
        bill = self.env['account.move'].sudo().create({
            'move_type': 'in_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': self.date,
            'invoice_origin': self.origin or self.name,
            'ref': self.name,
            'cl_farmer_season_id': fs.id,
            'company_id': self.company_id.id,
            'invoice_line_ids': [(0, 0, {
                'name': self.env._(
                    "%(product)s bought from the farmer - lot %(lot)s", product=self.product_id.name, lot=self.lot_id.name),
                'product_id': self.product_id.id,
                'quantity': self.quantity,
                'price_unit': self.price_unit,
                'tax_ids': [(6, 0, [])],
                'analytic_distribution': fs._cl_analytic_distribution(
                    self.env.ref('cl_rice_mill.analytic_service_trading')),
            })],
        })
        bill.action_post()
        fs._cl_tag_receivable_lines(bill)
        self.bill_id = bill

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))
