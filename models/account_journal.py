from odoo import models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def _cl_method_line(self, payment_type):
        """Payment method of ``payment_type`` ('inbound' or 'outbound') that books the payment at once
        (it has an outstanding account)."""
        self.ensure_one()
        method_line = self._get_available_payment_method_lines(payment_type).filtered('payment_account_id')[:1]
        if not method_line:
            raise UserError(self.env._(
                "The journal %s has no outstanding account on its payment methods, so the payment cannot be "
                "booked. Run the Rice Mill setup or set one in the journal configuration.",
                self.display_name))
        return method_line

    def _cl_outbound_method_line(self):
        return self._cl_method_line('outbound')
