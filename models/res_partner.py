import re
import secrets

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import email_normalize

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
    cl_possible_duplicate_ids = fields.Many2many(
        'res.partner', string="Possible Duplicates", compute='_compute_cl_possible_duplicate_ids',
        help="Other farmers with the same phone number.")
    cl_farmer_season_ids = fields.One2many('cl.rice.farmer.season', 'partner_id', string="Farmer Seasons")
    cl_farmer_season_count = fields.Integer(compute='_compute_cl_farmer_season_count')

    _cl_nida_uniq = models.UniqueIndex(
        "(regexp_replace(upper(cl_nida_number), '[^0-9A-Z]', '', 'g'), COALESCE(company_id, 0)) "
        "WHERE cl_nida_number IS NOT NULL AND cl_nida_number != ''",
        "Another farmer is already registered with this National ID (NIDA).")
    _cl_client_uuid_uniq = models.UniqueIndex(
        '(cl_client_uuid) WHERE cl_client_uuid IS NOT NULL', "This field registration was already received.")
    _cl_farmer_code_uniq = models.UniqueIndex(
        '(cl_farmer_code, company_id) WHERE cl_farmer_code IS NOT NULL',
        "The farmer code must be unique.")

    @api.depends('phone', 'is_farmer')
    def _compute_cl_possible_duplicate_ids(self):
        for partner in self:
            partner.cl_possible_duplicate_ids = partner.is_farmer and self._cl_find_duplicates(
                phone=partner.phone, exclude=partner._origin)['phone']

    @api.model
    def _cl_normalize_phone(self, phone):
        """Last 9 digits of a Tanzanian number: 0712 345 678, +255 712 345 678 and 712345678 match."""
        digits = re.sub(r'\D', '', phone or '')
        return digits[-9:] if len(digits) >= 9 else ''

    @api.model
    def _cl_normalize_nida(self, nida):
        return re.sub(r'[^0-9A-Z]', '', (nida or '').upper())

    @api.model
    def _cl_find_duplicates(self, nida=None, phone=None, exclude=None):
        """Farmers already registered with the same NIDA or phone (same company).

        Used by every registration path: the farmer form, the field app, the opening balance
        import and the website. Runs as superuser: a field officer must be warned about a
        duplicate even if the other farmer is not his.
        :return: {'nida': partners, 'phone': partners}
        """
        Partner = self.sudo().with_context(active_test=False)
        result = {'nida': Partner.browse(), 'phone': Partner.browse()}
        exclude_ids = (exclude or Partner.browse()).ids
        base = Domain('is_farmer', '=', True) & Domain('id', 'not in', exclude_ids)
        if nida and self._cl_normalize_nida(nida):
            candidates = Partner.search(base & Domain('cl_nida_number', '!=', False))
            target = self._cl_normalize_nida(nida)
            result['nida'] = candidates.filtered(lambda p: self._cl_normalize_nida(p.cl_nida_number) == target)
        normalized = self._cl_normalize_phone(phone)
        if normalized:
            candidates = Partner.search(base & Domain('phone', '!=', False))
            result['phone'] = candidates.filtered(lambda p: self._cl_normalize_phone(p.phone) == normalized)
        return result

    @api.constrains('cl_nida_number', 'is_farmer')
    def _check_cl_nida_unique(self):
        for partner in self.filtered(lambda p: p.is_farmer and p.sudo().cl_nida_number):
            duplicates = self._cl_find_duplicates(nida=partner.sudo().cl_nida_number, exclude=partner)['nida']
            duplicates = duplicates.filtered(lambda p: p.company_id == partner.company_id)
            if duplicates:
                raise ValidationError(self.env._(
                    "Farmer %(other)s is already registered with this National ID (NIDA).", other=duplicates[0].name))

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

    def action_cl_grant_portal(self):
        """One click: the farmer gets a portal login (email, or phone number when there is no email) to
        follow the account. A first password is shown once, to give to the farmer."""
        self.ensure_one()
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only the office can give portal access."))
        if not self.is_farmer:
            raise UserError(self.env._("Only farmers get access to the rice mill portal."))
        user = self.with_context(active_test=False).user_ids[:1]
        if user and not user._is_portal():
            raise UserError(self.env._("%s is an internal user.", self.name))
        if user and user.active:
            raise UserError(self.env._("%s already has portal access (login: %s).", self.name, user.login))
        login = self.email and email_normalize(self.email) or self._cl_normalize_phone_login()
        if not login:
            raise UserError(self.env._("Enter the farmer's phone number or email first: it is the portal login."))
        password = secrets.token_urlsafe(6)
        portal = self.env.ref('base.group_portal')
        if user:
            user.sudo().write({'active': True, 'password': password, 'group_ids': [(4, portal.id)]})
        else:
            company = self.company_id or self.env.company
            user = self.env['res.users'].sudo().with_context(no_reset_password=True)._create_user_from_template({
                'login': login,
                'partner_id': self.id,
                'company_id': company.id,
                'company_ids': [(6, 0, company.ids)],
                'password': password,
            })
        self.message_post(body=self.env._("Portal access given by %(user)s (login %(login)s).",
                                          user=self.env.user.name, login=user.login))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._("Portal access ready"),
                'message': self.env._(
                    "Give the farmer: website %(url)s/my/rice, login %(login)s, password %(password)s. "
                    "The farmer can change the password after logging in.",
                    url=self.get_base_url(), login=user.login, password=password),
                'sticky': True,
                'type': 'success',
            },
        }

    def _cl_normalize_phone_login(self):
        digits = re.sub(r'\D', '', self.phone or '')
        if len(digits) >= 9:
            return '255' + digits[-9:]
        return False

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_rice_notify(self, event, records):
        """Hook called on farmer-facing events: loan_disbursed, service_charged, intake_done, milling_done,
        release_cleared, payment_received, settlement_paid and balance (on request).
        Messaging bridges (WhatsApp, SMS) override it and return True when a message was sent;
        the core sends nothing."""
        return False
