from odoo import fields, models


class ClRiceLoanInterest(models.Model):
    _name = 'cl.rice.loan.interest'
    _description = "Loan Interest"
    _order = 'loan_id, period'

    loan_id = fields.Many2one('cl.rice.loan', required=True, ondelete='cascade', index=True)
    farmer_season_id = fields.Many2one(related='loan_id.farmer_season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='loan_id.field_officer_id', store=True)
    company_id = fields.Many2one(related='loan_id.company_id', store=True)
    currency_id = fields.Many2one(related='loan_id.currency_id')
    period = fields.Char(required=True, help="FLAT for flat interest, Mnn for the n-th month of a monthly loan.")
    amount = fields.Monetary(required=True)
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True)
    date = fields.Date(related='move_id.date', store=True)

    # makes the interest accrual idempotent: a period is charged once
    _loan_period_uniq = models.Constraint('UNIQUE(loan_id, period)', "This interest period is already charged.")
