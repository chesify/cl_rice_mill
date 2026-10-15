from odoo import api, fields, models


class ClRiceFarmerGroup(models.Model):
    _name = 'cl.rice.farmer.group'
    _description = "Farmer Group (Kikundi)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(required=True, tracking=True)
    leader_id = fields.Many2one('res.partner', string="Leader (Kiongozi)", domain="[('is_farmer', '=', True)]")
    village = fields.Char(string="Village (Kijiji)")
    member_ids = fields.One2many('res.partner', 'cl_farmer_group_id', string="Members")
    member_count = fields.Integer(compute='_compute_member_count')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)
    note = fields.Text()

    @api.depends('member_ids')
    def _compute_member_count(self):
        for group in self:
            group.member_count = len(group.member_ids)
