from odoo import fields, models

SETTLEMENT_CATEGORIES = [
    ('loan', "Loan (Mkopo)"),
    ('interest', "Interest (Riba)"),
    ('service', "Services"),
    ('operator', "Operators Paid"),
    ('milling', "Milling (Kukoboa)"),
    ('storage', "Storage (Ghala)"),
    ('other_charge', "Other Charges"),
    ('proceeds', "Sale Proceeds (Mauzo)"),
    ('byproduct', "By-product Credit"),
    ('purchase', "Crop Bought by the Mill"),
    ('other_credit', "Other Credits"),
]


class ClRiceSettlementLine(models.Model):
    _name = 'cl.rice.settlement.line'
    _description = "Settlement Line"
    _order = 'settlement_id, kind desc, sequence, id'

    settlement_id = fields.Many2one('cl.rice.settlement', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='settlement_id.company_id', store=True)
    currency_id = fields.Many2one(related='settlement_id.currency_id')
    sequence = fields.Integer(default=10)
    kind = fields.Selection([('owed', "Owed by the Farmer"), ('earned', "Due to the Farmer")], required=True)
    category = fields.Selection(SETTLEMENT_CATEGORIES, required=True)
    date = fields.Date()
    name = fields.Char(string="Description", required=True)
    amount = fields.Monetary(required=True)
    move_line_id = fields.Many2one('account.move.line', string="Journal Item", readonly=True)
