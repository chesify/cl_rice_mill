from odoo import fields, models
from odoo.exceptions import UserError

# context key set only by the rice mill's own transfers (intake, milling, release, opening balance...)
RICE_TRANSFER_KEY = 'cl_rice_controlled_transfer'


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    cl_bags = fields.Float(string="Bags", digits=(16, 1), help="Number of bags moved.")

    _cl_bags_positive = models.Constraint('CHECK(cl_bags >= 0)', "The number of bags cannot be negative.")

    def _action_done(self):
        if not self.env.context.get(RICE_TRANSFER_KEY):
            self._cl_check_rice_stock_leaving()
        return super()._action_done()

    def _cl_check_rice_stock_leaving(self):
        """Stock owned by farmers (or walk-in customers) only leaves the rice mill warehouse through a
        cleared stock release or a milling job, never through a plain transfer."""
        companies = self.company_id
        stores = {company.id: company._cl_rice_store_locations() for company in companies}
        partitions = {
            company.id: company.cl_rice_paddy_location_id | company.cl_rice_rice_location_id for company in companies
        }
        is_manager = self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager')
        for line in self:
            store = stores.get(line.company_id.id)
            if not store or not line.location_id._child_of(store) or line.location_dest_id._child_of(store):
                continue
            owner = line.owner_id
            owned = owner and owner != line.company_id.partner_id
            if owned or line.lot_id.cl_owner_id:
                raise UserError(self.env._(
                    "%(product)s of lot %(lot)s belongs to %(owner)s: it can only leave the warehouse through a "
                    "cleared stock release (Release Stock) or a milling job.",
                    product=line.product_id.display_name, lot=line.lot_id.name or '-',
                    owner=(owner or line.lot_id.cl_owner_id).name))
            if not is_manager and any(line.location_id._child_of(p) for p in partitions[line.company_id.id]):
                raise UserError(self.env._(
                    "Only a Rice Mill Manager can validate a transfer out of the paddy or milled rice partitions."))
