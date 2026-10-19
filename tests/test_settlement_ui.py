from odoo.tests import HttpCase, tagged

from .common import ClRiceCommon
from .test_dashboard import WAIT_JS


@tagged('post_install', '-at_install')
class TestSettlementUi(ClRiceCommon, HttpCase):
    """An interim payout where the farmer still owes can be approved and paid from the form (N16)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        cls._intake(cls.fs)
        cls._mill(cls.fs)  # 50,000 milling fee owed
        cls.fs.sudo().credit_limit_override = 10_000_000
        cls._disburse(cls._create_loan(cls.fs, 3_610_000))
        buyer = cls.env['res.partner'].create({'name': "Mbeya Traders"})
        sale = cls.env['cl.rice.facilitated.sale'].create({
            'farmer_season_id': cls.fs.id, 'buyer_id': buyer.id, 'product_id': cls.rice.id,
            'quantity': 600, 'price_unit': 10_000, 'payment_route': 'luvanda'})
        cls._receive(cls.fs, 2_000_000, 'buyer', sale_id=sale.id)

    def test_approve_and_pay_out_with_debt(self):
        action = self.fs.with_user(self.user_accountant).action_open_settle()
        wizard = self.env['cl.rice.settlement.create'].with_user(self.user_accountant).with_context(
            action['context']).create({'payout_journal_id': self.cash_journal.id, 'settlement_type': 'interim',
                                       'payout_amount': 500_000})
        settlement = self.env['cl.rice.settlement'].browse(wizard.action_create()['res_id'])
        self.assertLess(settlement.net_amount, 0)
        self.assertEqual(settlement.negative_policy, 'keep')
        self.assertTrue(settlement.needs_debt_approval)
        # as the interim payouts made before the fix: the hidden next season must not be required
        settlement.sudo().negative_policy = 'carry_forward'
        code = WAIT_JS + """
            const waitGone = async (selector) => {
                for (let i = 0; i < 300; i++) {
                    if (!document.querySelector(selector)) { return; }
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                throw new Error(`${selector} is still there`);
            };
            const failOnNotification = () => {
                const danger = document.querySelector('.o_notification.border-danger, .o_notification_bar.bg-danger');
                if (danger) { throw new Error('notification: ' + danger.textContent); }
            };
            (async () => {
                (await wait('.o_form_view button[name="action_open_payout_with_debt"]')).click();
                const reason = await wait('.modal .o_field_widget[name="reason"] textarea');
                failOnNotification();
                reason.value = 'Rice in store covers the rest';
                reason.dispatchEvent(new Event('input', { bubbles: true }));
                reason.dispatchEvent(new Event('change', { bubbles: true }));
                document.querySelector('.modal button[name="action_confirm"]').click();
                await waitGone('.modal');
                await waitGone('.o_form_view button[name="action_open_payout_with_debt"]');
                failOnNotification();
                (await wait('.o_form_view .o_form_statusbar button[name="action_confirm"]')).click();
                (await wait('.modal-footer .btn-primary')).click();
                for (let i = 0; i < 300; i++) {
                    failOnNotification();
                    const current = document.querySelector('.o_statusbar_status .o_arrow_button_current');
                    if (current && current.textContent.includes('Paid')) {
                        console.log('test successful');
                        return;
                    }
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                throw new Error('the payout was not made');
            })().catch((error) => console.error(error.message));
        """
        self.browser_js(f'/odoo/action-cl_rice_mill.cl_rice_settlement_action/{settlement.id}', code,
                        login=self.user_manager.login, timeout=120)
        settlement.invalidate_recordset()
        self.assertEqual(settlement.state, 'paid')
        self.assertEqual(settlement.debt_approval_uid, self.user_manager)
        self.assertEqual(settlement.payment_id.amount, 500_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 2_160_000)
