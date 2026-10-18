from odoo import fields, models


class StockLot(models.Model):
    _inherit = 'stock.lot'

    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index='btree_not_null', readonly=True, copy=False)
    cl_season_id = fields.Many2one('cl.rice.season', string="Season", index='btree_not_null', readonly=True, copy=False)
    cl_owner_id = fields.Many2one(
        'res.partner', string="Stock Owner", index='btree_not_null', readonly=True, copy=False,
        help="Owner of the stock of this lot. Empty: the mill owns it.")
    cl_rice_product = fields.Selection(related='product_id.cl_rice_product')
    cl_bag_qty = fields.Float(string="Bags Held", compute='_compute_cl_held', digits=(16, 1))
    cl_kg_held = fields.Float(string="Kg Held", compute='_compute_cl_held', digits='Product Unit')
    cl_first_in_date = fields.Datetime(
        string="First Stored On", compute='_compute_cl_held',
        help="Date the stock of this lot first entered the stores; the free storage period starts then.")

    def _compute_cl_held(self):
        lines = self.env['stock.move.line'].sudo().search_fetch(
            [('lot_id', 'in', self.ids), ('state', '=', 'done')],
            ['lot_id', 'cl_bags', 'date', 'location_id', 'location_dest_id'],
            order='date',
        )
        bags, first_in = {}, {}
        for line in lines:
            into_store = line.location_dest_id.usage == 'internal' and line.location_id.usage != 'internal'
            out_of_store = line.location_id.usage == 'internal' and line.location_dest_id.usage != 'internal'
            if into_store:
                bags[line.lot_id.id] = bags.get(line.lot_id.id, 0.0) + line.cl_bags
                first_in.setdefault(line.lot_id.id, line.date)
            elif out_of_store:
                bags[line.lot_id.id] = bags.get(line.lot_id.id, 0.0) - line.cl_bags
        quants = self.env['stock.quant'].sudo()._read_group(
            [('lot_id', 'in', self.ids), ('location_id.usage', '=', 'internal')],
            ['lot_id'], ['quantity:sum'])
        kgs = {lot.id: quantity for lot, quantity in quants}
        for lot in self:
            lot.cl_kg_held = kgs.get(lot.id, 0.0)
            lot.cl_bag_qty = max(bags.get(lot.id, 0.0), 0.0) if lot.cl_kg_held > 0 else 0.0
            lot.cl_first_in_date = first_in.get(lot.id, False)

    def _cl_bags_for_kg(self, kg):
        """Bags taken out with ``kg`` of this lot, at the lot's average weight per bag."""
        self.ensure_one()
        if not self.cl_bag_qty or not self.cl_kg_held:
            return 0.0
        return min(round(kg * self.cl_bag_qty / self.cl_kg_held, 1), self.cl_bag_qty)

    def _cl_bags_for_fee(self):
        """Bags to charge storage on: the recorded bag balance, or an estimate from the weight."""
        self.ensure_one()
        if self.cl_bag_qty:
            return self.cl_bag_qty
        company = self.company_id or self.env.company
        bag_kg = company.cl_rice_paddy_bag_kg if self.cl_rice_product == 'paddy' else company.cl_rice_rice_bag_kg
        if not bag_kg:
            return 0.0
        return -(-self.cl_kg_held // bag_kg)  # ceiling: a started bag counts
