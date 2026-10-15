from odoo import models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def _cl_outbound_method_line(self):
        """Outgoing payment method that books the payment at once (it has an outstanding account)."""
        self.ensure_one()
        method_line = self._get_available_payment_method_lines('outbound').filtered('payment_account_id')[:1]
        if not method_line:
            raise UserError(self.env._(
                "The journal %s has no outstanding payments account on its outgoing payment methods, so the "
                "payment cannot be booked. Set one in the journal configuration (Outgoing Payments tab).",
                self.display_name))
        return method_line
