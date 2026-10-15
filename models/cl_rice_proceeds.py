from odoo import fields, models


class ClRiceProceeds(models.Model):
    _name = 'cl.rice.proceeds'
    _description = "Farmer Sale Proceeds"
    _order = 'date desc, id desc'

    invoice_line_id = fields.Many2one('account.move.line', string="Invoice Line", required=True, index=True, readonly=True)
    invoice_id = fields.Many2one(related='invoice_line_id.move_id', store=True, string="Buyer Invoice")
    farmer_season_id = fields.Many2one('cl.rice.farmer.season', string="Farmer Season", required=True, index=True, readonly=True)
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer")
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    quantity = fields.Float(digits='Product Unit', readonly=True)
    gross_amount = fields.Monetary(string="Sale Amount", readonly=True)
    commission_amount = fields.Monetary(string="Commission", readonly=True)
    net_amount = fields.Monetary(string="Due to Farmer", readonly=True)
    move_id = fields.Many2one('account.move', string="Proceeds Entry", readonly=True)

    _invoice_line_uniq = models.Constraint(
        'UNIQUE(invoice_line_id)', "The proceeds of this invoice line are already booked.")
