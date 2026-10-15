from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

SENSITIVE_GROUPS = 'cl_rice_mill.cl_rice_mill_group_accountant'


class ResPartner(models.Model):
    _inherit = 'res.partner'
    _rec_names_search = ['complete_name', 'email', 'ref', 'vat', 'cl_farmer_code']

    is_farmer = fields.Boolean(string="Is a Farmer", index=True, tracking=True)
    cl_farmer_code = fields.Char(string="Farmer Code", copy=False, readonly=True, index='btree_not_null')
    cl_farmer_state = fields.Selection(
        [('pending', "Pending Review"), ('approved', "Approved"), ('blocked', "Blocked")],
        string="Farmer Status", default='approved', tracking=True)
    cl_field_officer_id = fields.Many2one(
        'res.users', string="Field Officer", tracking=True, index='btree_not_null',
        domain="[('share', '=', False)]")
    cl_nida_number = fields.Char(string="National ID (NIDA)", groups=SENSITIVE_GROUPS, copy=False)
    cl_whatsapp = fields.Char(string="WhatsApp Number")
    cl_village = fields.Char(string="Village")
    cl_ward = fields.Char(string="Ward")
    cl_district = fields.Char(string="District")
    cl_region = fields.Char(string="Region")
    cl_farm_latitude = fields.Float(string="Farm Latitude", digits=(10, 7))
    cl_farm_longitude = fields.Float(string="Farm Longitude", digits=(10, 7))
    cl_total_acreage = fields.Float(string="Total Acreage", digits=(16, 2))
    cl_farmer_group_id = fields.Many2one('cl.rice.farmer.group', string="Farmer Group", index='btree_not_null')
    cl_guarantor_id = fields.Many2one('res.partner', string="Guarantor")
    cl_next_of_kin = fields.Char(string="Next of Kin")
    cl_next_of_kin_phone = fields.Char(string="Next of Kin Phone")
    cl_payout_method = fields.Selection(
        [
            ('cash', "Cash"),
            ('mpesa', "M-Pesa"),
            ('tigopesa', "Tigo Pesa"),
            ('airtel', "Airtel Money"),
            ('bank', "Bank Transfer"),
        ],
        string="Payout Method", default='cash', groups=SENSITIVE_GROUPS, tracking=True)
    cl_payout_phone = fields.Char(string="Mobile Money Number", groups=SENSITIVE_GROUPS, tracking=True)
    cl_payout_bank_id = fields.Many2one(
        'res.partner.bank', string="Payout Bank Account", groups=SENSITIVE_GROUPS,
        domain="[('partner_id', '=', id)]")
    cl_client_uuid = fields.Char(
        string="Field App ID", readonly=True, copy=False,
        help="Identifier given by the field app; a registration sent twice is only created once.")
    cl_farmer_season_ids = fields.One2many('cl.rice.farmer.season', 'partner_id', string="Farmer Seasons")
    cl_farmer_season_count = fields.Integer(compute='_compute_cl_farmer_season_count')

    _cl_client_uuid_uniq = models.UniqueIndex(
        '(cl_client_uuid) WHERE cl_client_uuid IS NOT NULL', "This field registration was already received.")
    _cl_farmer_code_uniq = models.UniqueIndex(
        '(cl_farmer_code, company_id) WHERE cl_farmer_code IS NOT NULL',
        "The farmer code must be unique.")

    def _compute_cl_farmer_season_count(self):
        counts = dict(self.env['cl.rice.farmer.season']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count']))
        for partner in self:
            partner.cl_farmer_season_count = counts.get(partner, 0)

    @api.constrains('cl_farm_latitude', 'cl_farm_longitude')
    def _check_cl_farm_coordinates(self):
        for partner in self:
            if not -90 <= partner.cl_farm_latitude <= 90 or not -180 <= partner.cl_farm_longitude <= 180:
                raise ValidationError(self.env._("The farm GPS coordinates are out of range."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('is_farmer'):
                if not vals.get('cl_field_officer_id') and self.env.user._is_internal():
                    vals['cl_field_officer_id'] = self.env.uid
        partners = super().create(vals_list)
        partners.filtered('is_farmer')._cl_assign_farmer_code()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_farmer'):
            self.filtered(lambda p: p.is_farmer)._cl_assign_farmer_code()
        return res

    def _cl_assign_farmer_code(self):
        Sequence = self.env['ir.sequence'].sudo()
        for partner in self.filtered(lambda p: not p.cl_farmer_code):
            company = partner.company_id or self.env.company
            partner.cl_farmer_code = Sequence.with_company(company).next_by_code('cl.rice.farmer')
            partner._cl_set_farmer_accounts(company)

    def _cl_set_farmer_accounts(self, company):
        """What farmers owe the mill is followed on the farmer service receivable."""
        if company.cl_rice_service_account_id:
            self.with_company(company).sudo().property_account_receivable_id = company.cl_rice_service_account_id

    def action_view_cl_farmer_seasons(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_farmer_season_action')
        action['domain'] = [('partner_id', '=', self.id)]
        action['context'] = {'default_partner_id': self.id}
        return action

    def action_cl_approve_farmer(self):
        self._cl_check_manager()
        self.cl_farmer_state = 'approved'

    def action_cl_block_farmer(self):
        self._cl_check_manager()
        self.cl_farmer_state = 'blocked'

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_rice_notify(self, event, records):
        """Hook called on farmer-facing events: loan_disbursed, service_charged, intake_done, milling_done,
        release_cleared, payment_received, settlement_paid and balance (on request).
        Messaging bridges (WhatsApp, SMS) override it and return True when a message was sent;
        the core sends nothing."""
        return False
