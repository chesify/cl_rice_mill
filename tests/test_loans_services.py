from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestLoans(ClRiceCommon):

    def _loan_lines(self, loan):
        return loan.payment_id.move_id.line_ids

    def test_loan_disbursement_entry(self):
        loan = self._disburse(self._create_loan(self.fs, 500_000))
        self.assertEqual(loan.state, 'disbursed')
        lines = self._loan_lines(loan)
        receivable = lines.filtered(lambda l: l.account_id == self.company.cl_rice_loan_account_id)
        self.assertEqual(receivable.debit, 500_000)
        self.assertEqual(receivable.partner_id, self.farmer)
        self.assertEqual(receivable.cl_farmer_season_id, self.fs)
        cash = lines.filtered(lambda l: l.account_id == self.outstanding_account)
        self.assertEqual(cash.credit, 500_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.loan_total, 500_000)
        self.assertEqual(self.fs.balance_owed, 500_000)
        self.assertEqual(self.fs.net_balance, -500_000)
        self.assertIn("disbursed", loan.message_ids[0].body)

    def test_loan_approval_rights(self):
        loan = self._create_loan(self.fs, 100_000)
        loan.action_submit()
        with self.assertRaises(AccessError):
            loan.with_user(self.user_accountant).action_approve()
        loan.with_user(self.user_manager).action_approve()
        self.assertEqual(loan.state, 'approved')
        self.assertEqual(loan.approved_uid, self.user_manager)
        with self.assertRaises(AccessError):
            loan.with_user(self.user_officer).action_disburse()

    def test_field_officer_loans(self):
        loan = self.env['cl.rice.loan'].with_user(self.user_officer).create({
            'farmer_season_id': self.fs.id,
            'amount': 100_000,
        })
        loan.action_submit()
        self.assertEqual(loan.state, 'to_approve')
        with self.assertRaises(AccessError):
            self.env['cl.rice.loan'].with_user(self.user_officer).create({
                'farmer_season_id': self.fs_2.id,
                'amount': 100_000,
            })

    def test_loan_above_credit_limit_needs_manager_reason(self):
        # credit limit is 2,000,000
        self._disburse(self._create_loan(self.fs, 1_500_000))
        loan = self._create_loan(self.fs, 800_000)
        loan.action_submit()
        self.assertTrue(loan.over_limit)
        action = loan.with_user(self.user_manager).action_approve()
        self.assertEqual(action['res_model'], 'cl.rice.reason')
        self.assertEqual(loan.state, 'to_approve')
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.loan',
            'res_id': loan.id,
            'action': 'approve_over_limit',
            'reason': "Harvest looks excellent",
        }).action_confirm()
        self.assertEqual(loan.state, 'approved')
        self.assertEqual(loan.approval_reason, "Harvest looks excellent")
        self.assertTrue(any("Harvest looks excellent" in m.body for m in loan.message_ids))
        loan.with_user(self.user_accountant).action_disburse()
        self.fs.invalidate_recordset()
        self.assertTrue(self.fs.over_limit)

    def test_flat_interest(self):
        self.company.write({'cl_rice_interest_mode': 'flat', 'cl_rice_interest_rate': 10.0})
        loan = self._disburse(self._create_loan(self.fs, 300_000))
        self.assertEqual(loan.interest_total, 30_000)
        move = loan.interest_ids.move_id
        income = move.line_ids.filtered(lambda l: l.account_id == self.company.cl_rice_interest_income_account_id)
        self.assertEqual(income.credit, 30_000)
        key = next(iter(income.analytic_distribution))
        self.assertIn(str(self.fs.analytic_account_id.id), key.split(','))
        self.assertEqual(len(income.analytic_line_ids), 1)
        self.assertEqual(income.analytic_line_ids.amount, 30_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 330_000)

    def test_monthly_interest_cron_is_idempotent(self):
        self.company.write({
            'cl_rice_interest_mode': 'monthly',
            'cl_rice_interest_rate': 2.0,
            'cl_rice_interest_accrual': 'cron',
        })
        loan = self._disburse(self._create_loan(self.fs, 1_000_000))
        today = fields.Date.context_today(loan)
        loan.disbursement_date = today - relativedelta(months=3, days=5)
        Loan = self.env['cl.rice.loan']
        Loan._cron_accrue_interest()
        Loan._cron_accrue_interest()
        self.assertEqual(loan.interest_ids.mapped('period'), ['M01', 'M02', 'M03'])
        self.assertEqual(loan.interest_total, 60_000)
        # at settlement the started fourth month is charged too
        loan._cl_accrue_interest_at_settlement(today)
        loan._cl_accrue_interest_at_settlement(today)
        self.assertEqual(loan.interest_total, 80_000)

    def test_loan_reversal(self):
        loan = self._disburse(self._create_loan(self.fs, 200_000))
        with self.assertRaises(UserError):
            loan.action_cancel()
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.loan',
            'res_id': loan.id,
            'action': 'reverse',
            'reason': "Paid to the wrong farmer",
        }).action_confirm()
        self.assertEqual(loan.state, 'cancelled')
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)

    def test_no_loan_on_settled_season(self):
        self.fs.with_context(cl_rice_unlock=True).state = 'settled'
        with self.assertRaises(UserError):
            self._create_loan(self.fs, 1000)


@tagged('post_install', '-at_install')
class TestServiceJobs(ClRiceCommon):

    def _job(self, **vals):
        return self.env['cl.rice.service.job'].create({
            'farmer_season_id': self.fs.id,
            'service_type': 'ploughing',
            'date': '2026-08-01',
            'vehicle_id': self.tractor.id,
            'operator_id': self.operator.id,
            'quantity': 4,
            **vals,
        })

    def test_rate_lookup(self):
        job = self._job()
        self.assertEqual(job.rate_id, self.rate_ploughing)
        self.assertEqual(job.uom, 'acre')
        self.assertEqual(job.price_unit, 50_000)
        self.assertEqual(job.amount, 200_000)

    def test_service_charge_invoice(self):
        job = self._job(fuel_litres=20, fuel_cost=60_000)
        job.action_done()
        with self.assertRaises(AccessError):
            job.with_user(self.user_officer).action_charge()
        job.with_user(self.user_accountant).action_charge()
        self.assertEqual(job.state, 'charged')
        invoice = job.invoice_id
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.amount_total, 200_000)
        self.assertEqual(invoice.cl_farmer_season_id, self.fs)
        line = invoice.invoice_line_ids
        self.assertEqual(line.account_id, self.company.cl_rice_tractor_income_account_id)
        self.assertEqual(line.vehicle_id, self.tractor)
        machine_account = self.tractor.cl_analytic_account_id
        self.assertTrue(machine_account)
        expected_key = ",".join(str(i) for i in (
            self.fs.analytic_account_id | self.env.ref('cl_rice_mill.analytic_service_tractor') | machine_account
        ).ids)
        self.assertEqual(line.analytic_distribution, {expected_key: 100.0})
        receivable = invoice.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(receivable.account_id, self.company.cl_rice_service_account_id)
        self.assertEqual(receivable.cl_farmer_season_id, self.fs)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.service_total, 200_000)
        self.assertEqual(self.fs.balance_owed, 200_000)

    def test_operator_paid_on_behalf(self):
        job = self._job(
            service_type='harvesting', rate_id=False, price_unit=80_000, quantity=4,
            operator_paid_amount=40_000, operator_payment_mode='cash', operator_journal_id=self.cash_journal.id,
        )
        job.action_done()
        job.with_user(self.user_accountant).action_charge()
        entry = job.operator_move_id
        receivable = entry.line_ids.filtered(lambda l: l.account_id == self.company.cl_rice_service_account_id)
        self.assertEqual(receivable.debit, 40_000)
        self.assertEqual(receivable.partner_id, self.farmer)
        payable = entry.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')
        self.assertEqual(payable.partner_id, self.operator.work_contact_id)
        self.assertTrue(payable.reconciled, "the cash payment to the operator settles the payable")
        self.assertEqual(job.invoice_id.invoice_line_ids.account_id, self.company.cl_rice_harvester_income_account_id)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.operator_total, 40_000)
        self.assertEqual(self.fs.balance_owed, 360_000)

    def test_free_service_keeps_cost(self):
        job = self._job(is_free=True, fuel_cost=30_000, operator_cost=10_000)
        self.assertEqual(job.amount, 0)
        job.action_done()
        job.action_charge()
        self.assertEqual(job.state, 'charged')
        self.assertFalse(job.invoice_id)
        self.assertEqual(job.cost_total, 40_000)

    def test_charged_job_reversal(self):
        job = self._job()
        job.action_done()
        job.action_charge()
        with self.assertRaises(UserError):
            job.write({'quantity': 10})
        with self.assertRaises(AccessError):
            job.with_user(self.user_accountant)._cl_reverse("x")
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.service.job',
            'res_id': job.id,
            'action': 'reverse',
            'reason': "Wrong farmer",
        }).action_confirm()
        self.assertEqual(job.state, 'cancelled')
        self.assertEqual(job.invoice_id.payment_state, 'reversed')
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)

    def test_no_charge_on_settled_season(self):
        job = self._job()
        job.action_done()
        self.fs.with_context(cl_rice_unlock=True).state = 'settled'
        with self.assertRaises(UserError):
            job.action_charge()
        with self.assertRaises(UserError):
            self._job()
