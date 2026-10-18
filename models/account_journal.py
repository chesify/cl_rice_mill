from odoo import api, models
from odoo.exceptions import UserError
from odoo.fields import Domain


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    @api.model
    def _cl_for_method(self, method, company):
        """Default cash / bank / mobile money journal (M-Pesa first) of the company."""
        codes = {'cash': ['CSH'], 'bank': ['BNK'], 'mobile_money': ['MPS', 'TGP', 'ATM']}[method]
        domain = Domain(self._check_company_domain(company)) & Domain('type', 'in', ('cash', 'bank'))
        journals = self.search(domain & Domain('code', 'in', codes))
        for code in codes:
            journal = journals.filtered(lambda j: j.code == code)[:1]
            if journal:
                return journal
        if method == 'mobile_money':
            return self.search(domain & Domain('name', 'ilike', 'pesa'), limit=1) or self.browse()
        return self.search(domain & Domain('type', '=', 'cash' if method == 'cash' else 'bank'), limit=1)

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
