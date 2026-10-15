from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index='btree_not_null',
        domain="[('state', 'in', ('harvested', 'in_storage', 'sold')), ('company_id', '=', company_id)]",
        help="Sell this farmer's own stock on their behalf.")
    cl_lot_id = fields.Many2one(
        'stock.lot', string="Farmer Lot",
        compute='_compute_cl_lot_id', store=True, readonly=False,
        domain="[('product_id', '=', product_id), ('cl_farmer_season_id', '=', cl_farmer_season_id)]")
    cl_sale_mode = fields.Selection(
        [('agent', "Agent (on behalf)"), ('owner', "Owner (bought from the farmer)")],
        string="Sale Mode", compute='_compute_cl_sale_mode', store=True, readonly=False)
    cl_farmer_price = fields.Monetary(
        string="Price Paid to Farmer", compute='_compute_cl_farmer_price', store=True, readonly=False,
        help="Owner mode: price per unit the mill pays the farmer for the crop it buys.")
    cl_bags = fields.Float(string="Bags", digits=(16, 1))
    cl_ownership_transfer_id = fields.Many2one('cl.rice.ownership.transfer', string="Crop Purchase", readonly=True, copy=False)

    @api.depends('cl_farmer_season_id', 'product_id')
    def _compute_cl_lot_id(self):
        for line in self:
            if line.cl_farmer_season_id and line.product_id:
                line.cl_lot_id = line.cl_farmer_season_id._cl_find_lot(line.product_id)
            else:
                line.cl_lot_id = False

    @api.depends('cl_farmer_season_id')
    def _compute_cl_sale_mode(self):
        for line in self:
            line.cl_sale_mode = line.cl_farmer_season_id and line.order_id.company_id.cl_rice_sales_mode

    @api.depends('cl_farmer_season_id', 'product_id')
    def _compute_cl_farmer_price(self):
        for line in self:
            season = line.cl_farmer_season_id.season_id
            if line.product_id.cl_rice_product == 'paddy':
                line.cl_farmer_price = season.reference_price
            elif line.product_id.cl_rice_product == 'rice':
                line.cl_farmer_price = season.rice_reference_price
            else:
                line.cl_farmer_price = 0.0

    @api.constrains('cl_farmer_season_id', 'cl_lot_id', 'product_id')
    def _check_cl_farmer_lot(self):
        for line in self.filtered('cl_farmer_season_id'):
            if not line.product_id.cl_rice_product:
                raise ValidationError(self.env._("Only rice mill products can be sold on behalf of a farmer."))
            if line.cl_lot_id and line.cl_lot_id.cl_farmer_season_id != line.cl_farmer_season_id:
                raise ValidationError(self.env._(
                    "Lot %(lot)s does not belong to farmer season %(fs)s.",
                    lot=line.cl_lot_id.name, fs=line.cl_farmer_season_id.name))

    def _cl_check_farmer_stock(self):
        """No selling stock the farmer does not own."""
        Quant = self.env['stock.quant']
        requested = {}
        for line in self.filtered('cl_farmer_season_id'):
            fs = line.cl_farmer_season_id
            fs._cl_check_open()
            if not line.cl_lot_id:
                raise ValidationError(self.env._(
                    "Farmer %(farmer)s holds no %(product)s this season.",
                    farmer=fs.partner_id.name, product=line.product_id.name))
            qty = line.product_uom_id._compute_quantity(line.product_uom_qty, line.product_id.uom_id)
            requested[line.cl_lot_id] = requested.get(line.cl_lot_id, 0.0) + qty
        for lot, qty in requested.items():
            fs = lot.cl_farmer_season_id
            available = Quant._cl_available_qty(
                lot.product_id, lot, fs.partner_id, fs.company_id._cl_rice_store_locations())
            if qty > available + 0.0001:
                raise ValidationError(self.env._(
                    "Farmer %(farmer)s only owns %(available)s kg in lot %(lot)s: %(qty)s kg cannot be sold.",
                    farmer=fs.partner_id.name, available=round(available, 2), lot=lot.name, qty=round(qty, 2)))

    def _prepare_procurement_values(self):
        values = super()._prepare_procurement_values()
        if self.cl_farmer_season_id:
            values['cl_lot_id'] = self.cl_lot_id
            if self.cl_sale_mode == 'agent':
                values['restrict_partner_id'] = self.cl_farmer_season_id.partner_id
        return values

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        if self.cl_farmer_season_id and not self.display_type:
            fs = self.cl_farmer_season_id
            res['cl_farmer_season_id'] = fs.id
            if self.cl_sale_mode == 'agent':
                company = self.order_id.company_id
                res['account_id'] = company.cl_rice_clearing_account_id.id
                res['analytic_distribution'] = fs._cl_analytic_distribution()
            else:
                res['analytic_distribution'] = fs._cl_analytic_distribution(
                    self.env.ref('cl_rice_mill.analytic_service_trading'))
        return res

    def _cl_buy_from_farmer(self):
        """Owner mode: transfer the crop sold to the mill, billed by the farmer at the agreed price."""
        self._cl_check_farmer_stock()
        Transfer = self.env['cl.rice.ownership.transfer']
        for line in self.filtered(lambda l: not l.cl_ownership_transfer_id):
            transfer = Transfer.create({
                'farmer_season_id': line.cl_farmer_season_id.id,
                'lot_id': line.cl_lot_id.id,
                'quantity': line.product_uom_id._compute_quantity(line.product_uom_qty, line.product_id.uom_id),
                'bags': line.cl_bags,
                'price_unit': line.cl_farmer_price,
                'origin': line.order_id.name,
            })
            transfer.action_done()
            line.cl_ownership_transfer_id = transfer
