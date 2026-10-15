from odoo import fields, models
from odoo.tools import SQL


class ClRiceReportCashBook(models.Model):
    _name = 'cl.rice.report.cash.book'
    _description = "Cash Book"
    _auto = False
    _order = 'date desc, id desc'

    date = fields.Date(readonly=True)
    journal_id = fields.Many2one('account.journal', string="Cash / Bank / Mobile Money", readonly=True)
    partner_id = fields.Many2one('res.partner', string="Who", readonly=True)
    name = fields.Char(string="Description", readonly=True)
    move_id = fields.Many2one('account.move', string="Entry", readonly=True)
    money_in = fields.Monetary(string="Money In", readonly=True)
    money_out = fields.Monetary(string="Money Out", readonly=True)
    net = fields.Monetary(string="Net", readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)

    _depends = {
        'account.move.line': ['date', 'journal_id', 'partner_id', 'name', 'move_id', 'balance', 'account_id',
                              'parent_state', 'company_id', 'company_currency_id'],
    }

    @property
    def _table_sql(self):
        to_flush = [
            self.env[model]._fields[fname] for model, fnames in self._depends.items() for fname in fnames
        ]
        return SQL("""(
            SELECT aml.id,
                   aml.date,
                   aml.journal_id,
                   aml.partner_id,
                   aml.name,
                   aml.move_id,
                   GREATEST(aml.balance, 0) AS money_in,
                   GREATEST(-aml.balance, 0) AS money_out,
                   aml.balance AS net,
                   aml.company_id,
                   aml.company_currency_id AS currency_id
              FROM account_move_line aml
              JOIN account_journal journal ON journal.id = aml.journal_id
             WHERE aml.parent_state = 'posted'
               AND journal.type IN ('cash', 'bank')
               AND (aml.account_id = journal.default_account_id
                    OR aml.account_id IN (SELECT line.payment_account_id
                                            FROM account_payment_method_line line
                                           WHERE line.journal_id = journal.id
                                             AND line.payment_account_id IS NOT NULL))
        )""", to_flush=to_flush)
