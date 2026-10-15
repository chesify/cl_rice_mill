from collections import defaultdict

from odoo import api, fields, models
from odoo.fields import Domain


class ClRiceStorageFee(models.Model):
    _name = 'cl.rice.storage.fee'
    _description = "Storage Fee"
    _inherit = ['mail.thread']
    _order = 'period desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    farmer_season_id = fields.Many2one('cl.rice.farmer.season', string="Farmer Season", index=True, readonly=True)
    partner_id = fields.Many2one('res.partner', string="Owner", required=True, index=True, readonly=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    lot_id = fields.Many2one('stock.lot', string="Lot", required=True, index=True, readonly=True)
    product_id = fields.Many2one(related='lot_id.product_id', store=True)
    period = fields.Char(required=True, readonly=True, help="Month charged, YYYY-MM.")
    date = fields.Date(required=True, readonly=True)
    basis = fields.Selection([('bag', "Bags"), ('kg', "Kg")], required=True, readonly=True)
    quantity = fields.Float(digits='Product Unit', readonly=True)
    rate = fields.Monetary(readonly=True)
    amount = fields.Monetary(readonly=True)
    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    invoice_id = fields.Many2one('account.move', string="Invoice", readonly=True)

    # makes the monthly charge idempotent: a lot is charged once per month
    _lot_period_uniq = models.UniqueIndex('(lot_id, period)', "This lot is already charged for storage this month.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', self.env._("New")) == self.env._("New"):
                company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.storage.fee') or '/'
        return super().create(vals_list)

    @api.model
    def _cron_charge_storage(self, date=None):
        date = date or fields.Date.context_today(self)
        for company in self.env['res.company'].search([('cl_rice_storage_rate', '>', 0)]):
            self.with_company(company)._cl_charge_company(company, date)

    @api.model
    def _cl_charge_company(self, company, date):
        """Charge the storage of the month of ``date`` on every owned lot past its free period."""
        period = date.strftime('%Y-%m')
        locations = company._cl_rice_store_locations()
        if not locations:
            return self
        quants = self.env['stock.quant'].sudo().search(
            Domain([
                ('location_id', 'child_of', locations.ids),
                ('owner_id', 'not in', [False, company.partner_id.id]),
                ('lot_id', '!=', False),
                ('product_id.cl_rice_product', '!=', False),
                ('quantity', '>', 0),
            ]))
        held = defaultdict(float)
        for quant in quants:
            held[quant.lot_id, quant.owner_id] += quant.quantity
        charged_lots = set(self.sudo().search([('period', '=', period), ('company_id', '=', company.id)]).lot_id.ids)
        to_charge = defaultdict(list)  # (owner, farmer season) -> [fee vals]
        for (lot, owner), kg in held.items():
            if lot.id in charged_lots or not lot.cl_first_in_date:
                continue
            fs = lot.cl_farmer_season_id
            if fs.is_locked:
                continue
            if (date - lot.cl_first_in_date.date()).days <= company.cl_rice_storage_free_days:
                continue
            quantity = lot._cl_bags_for_fee() if company.cl_rice_storage_basis == 'bag' else kg
            amount = company.currency_id.round(quantity * company.cl_rice_storage_rate)
            if amount <= 0:
                continue
            to_charge[owner, fs].append({
                'farmer_season_id': fs.id,
                'partner_id': owner.id,
                'lot_id': lot.id,
                'period': period,
                'date': date,
                'basis': company.cl_rice_storage_basis,
                'quantity': quantity,
                'rate': company.cl_rice_storage_rate,
                'amount': amount,
                'company_id': company.id,
            })
        fees = self.sudo()
        for (owner, fs), vals_list in to_charge.items():
            new_fees = self.sudo().create(vals_list)
            new_fees._cl_invoice(owner, fs)
            fees |= new_fees
        return fees

    def _cl_invoice(self, owner, fs):
        company = self.company_id
        if not company.cl_rice_storage_income_account_id or not company.cl_rice_charge_journal_id:
            return
        storage_account = self.env.ref('cl_rice_mill.analytic_service_storage')
        distribution = (
            fs._cl_analytic_distribution(storage_account) if fs else {str(storage_account.id): 100.0}
        )
        basis_label = dict(self._fields['basis']._description_selection(self.env))
        invoice = self.env['account.move'].sudo().create({
            'move_type': 'out_invoice',
            'partner_id': owner.id,
            'journal_id': company.cl_rice_charge_journal_id.id,
            'invoice_date': self[:1].date,
            'invoice_origin': ", ".join(self.mapped('name')),
            'cl_farmer_season_id': fs.id,
            'company_id': company.id,
            'invoice_line_ids': [(0, 0, {
                'name': self.env._(
                    "Storage %(period)s - %(lot)s (%(qty)s %(basis)s)",
                    period=fee.period, lot=fee.lot_id.name, qty=fee.quantity, basis=basis_label[fee.basis]),
                'quantity': fee.quantity,
                'price_unit': fee.rate,
                'account_id': company.cl_rice_storage_income_account_id.id,
                'tax_ids': [(6, 0, [])],
                'analytic_distribution': distribution,
            }) for fee in self],
        })
        invoice.action_post()
        if fs:
            fs._cl_tag_receivable_lines(invoice)
        self.invoice_id = invoice
