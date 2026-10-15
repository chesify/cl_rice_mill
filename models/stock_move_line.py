from odoo import fields, models


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    cl_bags = fields.Float(string="Bags", digits=(16, 1), help="Number of bags (gunia) moved.")

    _cl_bags_positive = models.Constraint('CHECK(cl_bags >= 0)', "The number of bags cannot be negative.")
