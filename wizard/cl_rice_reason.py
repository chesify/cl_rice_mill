from odoo import api, fields, models
from odoo.exceptions import UserError

# models whose records may be acted on through this wizard; each implements _cl_reason_actions()
REASON_MODELS = [
    'cl.rice.farmer.season', 'cl.rice.loan', 'cl.rice.service.job', 'cl.rice.intake', 'cl.rice.settlement',
    'cl.rice.farmer.payment', 'cl.rice.stock.release', 'cl.rice.opening.balance',
]


class ClRiceReason(models.TransientModel):
    _name = 'cl.rice.reason'
    _description = "Rice Mill Action Reason"

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    action = fields.Char(required=True)
    reason = fields.Text(required=True)
    amount = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    show_amount = fields.Boolean(compute='_compute_show_amount')

    @api.model
    def _cl_reason_models(self):
        return REASON_MODELS

    @api.model
    def _cl_amount_actions(self):
        return {'credit_override'}

    @api.depends('action')
    def _compute_show_amount(self):
        amount_actions = self._cl_amount_actions()
        for wizard in self:
            wizard.show_amount = wizard.action in amount_actions

    def action_confirm(self):
        self.ensure_one()
        if self.res_model not in self._cl_reason_models():
            raise UserError(self.env._("This action is not available."))
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(self.env._("The document no longer exists."))
        record.check_access('write')
        handler = record._cl_reason_actions().get(self.action)
        if not handler:
            raise UserError(self.env._("This action is not available."))
        handler(self.reason, self.amount)
        return {'type': 'ir.actions.act_window_close'}
