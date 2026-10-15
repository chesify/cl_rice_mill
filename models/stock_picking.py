from odoo import api, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model
    def _cl_rice_done_transfer(self, picking_type, location, location_dest, owner, lines, origin, partner=None):
        """Create and validate a transfer moving exactly the given lots.

        :param owner: owner of the stock moved (a farmer or walk-in customer), empty for the mill
        :param lines: list of dicts with ``product``, ``qty``, ``lot`` and optionally ``bags``,
            ``owner`` (overrides the transfer owner for that line), ``value`` (value of a mill-owned
            incoming line) and ``quants`` (source quants of an outgoing line)
        :return: the done picking
        """
        Move = self.env['stock.move'].sudo()
        picking = self.sudo().create({
            'picking_type_id': picking_type.id,
            'location_id': location.id,
            'location_dest_id': location_dest.id,
            'partner_id': (partner or owner).id,
            'origin': origin,
            'company_id': picking_type.company_id.id,
        })
        moves = Move.create([{
            'product_id': line['product'].id,
            'product_uom_qty': line['qty'],
            'uom_id': line['product'].uom_id.id,
            'location_id': location.id,
            'location_dest_id': location_dest.id,
            'picking_id': picking.id,
            'restrict_partner_id': line.get('owner', owner).id,
            'cl_lot_id': line['lot'].id,
            'origin': origin,
            'company_id': picking.company_id.id,
        } for line in lines])
        moves._action_confirm(merge=False)
        moves.move_line_ids.unlink()
        values = []
        line_vals = []
        rounding = self.env['decimal.precision'].precision_get('Product Unit')
        for move, line in zip(moves, lines):
            line_owner = line.get('owner', owner)
            if line.get('value') is not None and not line_owner:
                values.append({'move_id': move.id, 'value': line['value'], 'company_id': move.company_id.id})
            sources = [(location, line['qty'])]
            if line.get('quants') is not None:
                sources, remaining = [], line['qty']
                for quant in line['quants']:
                    take = min(quant.quantity - quant.reserved_quantity, remaining)
                    if take > 0:
                        sources.append((quant.location_id, take))
                        remaining -= take
                    if remaining <= 0:
                        break
                if round(remaining, rounding) > 0:
                    raise UserError(self.env._(
                        "Not enough %(product)s in lot %(lot)s: %(missing)s kg missing.",
                        product=line['product'].display_name, lot=line['lot'].name, missing=round(remaining, rounding)))
            bags_left = line.get('bags', 0.0)
            for index, (source, qty) in enumerate(sources):
                bags = bags_left if index == len(sources) - 1 else round(line.get('bags', 0.0) * qty / line['qty'], 1)
                bags_left -= bags
                line_vals.append({
                    'move_id': move.id,
                    'picking_id': picking.id,
                    'product_id': move.product_id.id,
                    'uom_id': move.uom_id.id,
                    'lot_id': line['lot'].id,
                    'quantity': qty,
                    'location_id': source.id,
                    'location_dest_id': location_dest.id,
                    'owner_id': line_owner.id,
                    'cl_bags': bags,
                    'picked': True,
                    'company_id': move.company_id.id,
                })
        self.env['stock.move.line'].sudo().create(line_vals)
        if values:
            self.env['product.value'].sudo().create(values)
        moves.picked = True
        moves._action_done()
        return picking
