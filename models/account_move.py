from odoo import fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    cl_farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index='btree_not_null', copy=False, readonly=True)
    cl_proceeds_ids = fields.One2many('cl.rice.proceeds', 'invoice_id', string="Farmer Proceeds")

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted.filtered(lambda m: m.move_type in ('out_invoice', 'out_refund'))._cl_rice_book_proceeds()
        return posted

    def _cl_rice_farmer_lines(self):
        return self.invoice_line_ids.filtered(lambda l: l.cl_farmer_season_id and l.display_type == 'product')

    def _cl_rice_book_proceeds(self):
        """Agent mode: split each farmer line of a buyer invoice into proceeds due to the farmer
        and the mill's commission (Dr clearing / Cr farmer proceeds payable / Cr commission income)."""
        Proceeds = self.env['cl.rice.proceeds'].sudo()
        for move in self:
            farmer_lines = move._cl_rice_farmer_lines()
            if not farmer_lines:
                continue
            company = move.company_id
            farmer_lines.cl_farmer_season_id._cl_advance_state(('harvested', 'in_storage'), 'sold')
            agent_lines = farmer_lines.filtered(
                lambda l: l.account_id == company.cl_rice_clearing_account_id and not l.cl_proceeds_ids)
            if not agent_lines:
                continue
            if not company.cl_rice_proceeds_account_id or not company.cl_rice_commission_income_account_id:
                raise UserError(self.env._("Set the farmer proceeds and commission accounts in the Rice Mill settings."))
            sign = -1 if move.move_type == 'out_refund' else 1
            line_vals, proceeds_vals = [], []
            for line in agent_lines:
                fs = line.cl_farmer_season_id
                gross = abs(line.balance)
                qty = line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id)
                if company.cl_rice_commission_type == 'per_kg':
                    commission = qty * company.cl_rice_commission_value
                else:
                    commission = gross * company.cl_rice_commission_value / 100.0
                commission = min(company.currency_id.round(commission), gross)
                net = gross - commission
                label = self.env._(
                    "%(product)s sold for %(farmer)s - %(invoice)s",
                    product=line.product_id.name, farmer=fs.partner_id.name, invoice=move.name)
                line_vals.append({
                    'name': label,
                    'account_id': company.cl_rice_clearing_account_id.id,
                    'debit': gross if sign > 0 else 0.0,
                    'credit': gross if sign < 0 else 0.0,
                })
                line_vals.append({
                    'name': label,
                    'account_id': company.cl_rice_proceeds_account_id.id,
                    'partner_id': fs.partner_id.id,
                    'debit': net if sign < 0 else 0.0,
                    'credit': net if sign > 0 else 0.0,
                    'cl_farmer_season_id': fs.id,
                })
                if commission:
                    line_vals.append({
                        'name': self.env._("Commission on %s", label),
                        'account_id': company.cl_rice_commission_income_account_id.id,
                        'partner_id': fs.partner_id.id,
                        'debit': commission if sign < 0 else 0.0,
                        'credit': commission if sign > 0 else 0.0,
                        'analytic_distribution': fs._cl_analytic_distribution(
                            self.env.ref('cl_rice_mill.analytic_service_commission')),
                    })
                proceeds_vals.append({
                    'invoice_line_id': line.id,
                    'farmer_season_id': fs.id,
                    'date': move.date,
                    'product_id': line.product_id.id,
                    'quantity': sign * qty,
                    'gross_amount': sign * gross,
                    'commission_amount': sign * commission,
                    'net_amount': sign * net,
                })
            journal = company.cl_rice_settlement_journal_id
            if not journal:
                raise UserError(self.env._("Set the settlement journal in the Rice Mill settings."))
            entry = self.env['account.move'].sudo().create({
                'move_type': 'entry',
                'journal_id': journal.id,
                'date': move.date,
                'ref': self.env._("Farmer proceeds of %s", move.name),
                'company_id': company.id,
                'line_ids': [(0, 0, vals) for vals in line_vals],
            })
            entry.action_post()
            Proceeds.create([dict(vals, move_id=entry.id) for vals in proceeds_vals])
            for fs in agent_lines.cl_farmer_season_id:
                fs.partner_id._cl_rice_notify('sale_done', move)

    def button_draft(self):
        proceeds = self.cl_proceeds_ids
        if proceeds:
            locked = proceeds.farmer_season_id.filtered('is_locked')
            if locked:
                raise UserError(self.env._(
                    "This invoice carries proceeds of farmer season %s, which is settled. Reverse the settlement first.",
                    locked[0].name))
            entries = proceeds.move_id.filtered(lambda m: m.state == 'posted')
            entries.line_ids.remove_move_reconcile()
            entries._reverse_moves(cancel=True)
            proceeds.sudo().unlink()
        return super().button_draft()
