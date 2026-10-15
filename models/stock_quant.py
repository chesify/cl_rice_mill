from odoo import api, models
from odoo.fields import Domain


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _get_gather_domain(self, product_id, location_id, lot_id=None, package_id=None, owner_id=None, strict=False):
        domain = super()._get_gather_domain(product_id, location_id, lot_id, package_id, owner_id, strict)
        owner_ids = self.env.context.get('cl_rice_owner_ids')
        if owner_ids and not owner_id:
            domain = Domain(domain) & Domain('owner_id', 'in', owner_ids)
        return domain

    @api.model
    def _cl_owned_quants(self, product, lot, owner, locations):
        """Quants of ``product`` in ``lot`` owned by ``owner`` (the mill when empty) inside ``locations``."""
        company_partner = (locations.company_id[:1] or self.env.company).partner_id
        owner_domain = Domain('owner_id', '=', owner.id) if owner else Domain('owner_id', 'in', [False, company_partner.id])
        return self.sudo().search(
            Domain([
                ('product_id', '=', product.id),
                ('lot_id', '=', lot.id),
                ('location_id', 'child_of', locations.ids),
                ('quantity', '>', 0),
            ]) & owner_domain,
            order='in_date, id',
        )

    @api.model
    def _cl_available_qty(self, product, lot, owner, locations):
        quants = self._cl_owned_quants(product, lot, owner, locations)
        return sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))
