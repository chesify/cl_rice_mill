from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

SERVICE_TYPES = [
    ('ploughing', "Tractor Ploughing"),
    ('harrowing', "Harrowing"),
    ('harvesting', "Harvesting"),
    ('transport', "Transport"),
    ('storage', "Storage"),
    ('milling', "Milling (Kukoboa)"),
    ('bagging', "Bagging"),
    ('other', "Other"),
]

SERVICE_UNITS = [
    ('acre', "Acre"),
    ('hour', "Hour"),
    ('kg', "Kg"),
    ('bag', "Bag (Gunia)"),
    ('trip', "Trip"),
    ('month', "Month"),
]

# service type -> (company income account field, service line analytic xml id)
SERVICE_ACCOUNTING = {
    'ploughing': ('cl_rice_tractor_income_account_id', 'cl_rice_mill.analytic_service_tractor'),
    'harrowing': ('cl_rice_tractor_income_account_id', 'cl_rice_mill.analytic_service_tractor'),
    'harvesting': ('cl_rice_harvester_income_account_id', 'cl_rice_mill.analytic_service_harvester'),
    'storage': ('cl_rice_storage_income_account_id', 'cl_rice_mill.analytic_service_storage'),
    'milling': ('cl_rice_milling_income_account_id', 'cl_rice_mill.analytic_service_milling_farmer'),
    'transport': ('cl_rice_other_income_account_id', 'cl_rice_mill.analytic_service_transport'),
    'bagging': ('cl_rice_other_income_account_id', 'cl_rice_mill.analytic_service_transport'),
    'other': ('cl_rice_other_income_account_id', 'cl_rice_mill.analytic_service_transport'),
}


class ClRiceServiceRate(models.Model):
    _name = 'cl.rice.service.rate'
    _description = "Service Rate"
    _inherit = ['mail.thread']
    _order = 'service_type, date_from desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    service_type = fields.Selection(SERVICE_TYPES, required=True, tracking=True)
    uom = fields.Selection(SERVICE_UNITS, string="Unit", required=True, tracking=True)
    price_farmer = fields.Monetary(string="Farmer Price", required=True, tracking=True)
    price_walkin = fields.Monetary(string="Walk-in Price", tracking=True, help="Leave empty to use the farmer price.")
    date_from = fields.Date(string="Valid From", required=True, default=fields.Date.context_today, tracking=True)
    date_to = fields.Date(string="Valid To", tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    active = fields.Boolean(default=True)

    _price_positive = models.Constraint(
        'CHECK(price_farmer >= 0 AND (price_walkin IS NULL OR price_walkin >= 0))', "Prices cannot be negative.")
    _dates_check = models.Constraint(
        'CHECK(date_to IS NULL OR date_to >= date_from)', "The rate cannot end before it starts.")

    @api.depends('service_type', 'uom')
    def _compute_name(self):
        types = dict(self._fields['service_type']._description_selection(self.env))
        units = dict(self._fields['uom']._description_selection(self.env))
        for rate in self:
            rate.name = f"{types.get(rate.service_type, '')} / {units.get(rate.uom, '')}"

    @api.constrains('service_type', 'uom', 'date_from', 'date_to', 'company_id', 'active')
    def _check_overlap(self):
        for rate in self.filtered('active'):
            domain = Domain([
                ('id', '!=', rate.id),
                ('service_type', '=', rate.service_type),
                ('uom', '=', rate.uom),
                ('company_id', '=', rate.company_id.id),
            ]) & (Domain('date_to', '=', False) | Domain('date_to', '>=', rate.date_from))
            if rate.date_to:
                domain &= Domain('date_from', '<=', rate.date_to)
            if self.search_count(domain, limit=1):
                raise ValidationError(self.env._(
                    "Another %s rate is already valid during this period.", rate.name))

    @api.model
    def _cl_get_rate(self, service_type, date, company, uom=None):
        domain = Domain([
            ('service_type', '=', service_type),
            ('company_id', '=', company.id),
            ('date_from', '<=', date),
        ]) & (Domain('date_to', '=', False) | Domain('date_to', '>=', date))
        if uom:
            domain &= Domain('uom', '=', uom)
        return self.search(domain, order='date_from desc', limit=1)

    def _cl_price(self, customer_type='farmer'):
        self.ensure_one()
        if customer_type == 'walkin' and self.price_walkin:
            return self.price_walkin
        return self.price_farmer
