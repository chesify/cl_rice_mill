from odoo import fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    cl_lot_id = fields.Many2one(
        'stock.lot', string="Rice Mill Lot", index='btree_not_null', copy=False,
        help="Only this lot may be reserved for the move (farmer stock sold or milled).")

    def _update_reserved_quantity(self, need, location_id, lot_id=None, package_id=None, owner_id=None, strict=True):
        """Keep farmers' stock apart: a move restricted to an owner only takes that owner's stock
        (and its lot), and any other move of a rice mill product only takes the mill's own stock."""
        self.ensure_one()
        if not owner_id and self.restrict_partner_id:
            owner_id = self.restrict_partner_id
        if not lot_id and self.cl_lot_id:
            lot_id = self.cl_lot_id
        if not owner_id and self.product_id.cl_rice_product:
            self = self.with_context(cl_rice_owner_ids=[False, self.company_id.partner_id.id])  # noqa: PLW0642
        return super(StockMove, self)._update_reserved_quantity(
            need, location_id, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=strict)
