from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestReleaseSettlement(ClRiceCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        cls.buyer = cls.env['res.partner'].create({'name': "Mbeya Rice Traders"})
        cls.next_season = cls.env['cl.rice.season'].create({
            'name': "Season 2027/28", 'code': '2728', 'date_start': '2027-07-01', 'date_end': '2028-06-30',
            'reference_price': 1000,
        })
        for fs in (cls.fs, cls.fs_2):
            cls._intake(fs)
            cls._mill(fs)  # 650 kg rice held, milling fee 50,000 owed

    def _sale(self, fs, quantity=600, route='farmer', **vals):
        return self.env['cl.rice.facilitated.sale'].with_user(self.user_warehouse).create({
            'farmer_season_id': fs.id,
            'buyer_id': self.buyer.id,
            'product_id': self.rice.id,
            'quantity': quantity,
            'bags': quantity / 50,
            'price_unit': 2_500,
            'payment_route': route,
            'vehicle_plate': 'T 456 DEF',
            'driver_name': "Hamisi",
            **vals,
        })

    def _release(self, sale, quantity):
        return self.env['cl.rice.stock.release'].with_user(self.user_warehouse).create({
            'sale_id': sale.id, 'quantity': quantity, 'bags': quantity / 50,
            'vehicle_plate': sale.vehicle_plate, 'driver_name': sale.driver_name,
        })

    def _released_qty(self, partner):
        quants = self.env['stock.quant'].search([
            ('owner_id', '=', partner.id), ('product_id', '=', self.rice.id),
            ('location_id', '=', self.company.cl_rice_released_location_id.id)])
        return sum(quants.mapped('quantity')), quants

    # ------------------------------------------------------------------
    # Release control
    # ------------------------------------------------------------------
    def test_release_blocked_with_balance(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        release = self._release(self._sale(self.fs), 300)
        with self.assertRaisesRegex(UserError, "Release blocked"):
            release.action_clear()
        self.assertEqual(release.state, 'draft')
        with self.assertRaises(UserError):
            release.action_release()
        self.assertEqual(self._owned_qty(self.farmer, self.rice), 650)

    def test_release_allowed_after_full_payment(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        payment = self._receive(self.fs, 350_000)
        self.assertEqual(payment.applied_amount, 350_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)
        sale = self._sale(self.fs)
        release = self._release(sale, 300)
        release.action_clear()
        self.assertEqual(release.state, 'cleared')
        self.assertEqual(release.clearance_basis, 'no_balance')
        release.action_release()
        self.assertEqual(release.state, 'released')
        self.assertEqual(self._owned_qty(self.farmer, self.rice), 350)
        released, quants = self._released_qty(self.farmer)
        self.assertEqual(released, 300)
        self.assertTrue(all(value == 0 for value in quants.mapped('value')), "released stock never valued")
        self.assertEqual(sale.state, 'released')
        self.assertEqual(self.fs.state, 'released')

    def test_release_with_buyer_payment_covering_balance(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        sale = self._sale(self.fs, route='luvanda')
        # not enough yet
        self._receive(self.fs, 200_000, 'buyer', sale_id=sale.id)
        release = self._release(sale, 300)
        with self.assertRaisesRegex(UserError, "Release blocked"):
            release.action_clear()
        # the buyer pays the rest: the money held pays the farmer's balance first
        payment = self._receive(self.fs, 800_000, 'buyer', sale_id=sale.id)
        funds_line = payment.move_id.line_ids.filtered(lambda l: l.credit)
        self.assertEqual(funds_line.account_id, self.company.cl_rice_funds_held_account_id)
        self.assertEqual(funds_line.account_id.account_type, 'liability_payable', "held money is never income")
        self.assertEqual(payment.payer_id, self.buyer)
        release.action_clear()
        self.assertEqual(release.clearance_basis, 'funds')
        self.assertEqual(release.funds_applied, 350_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self.fs.funds_held, 650_000)
        self.assertEqual(sale.buyer_paid, 1_000_000)

    def test_release_with_manager_override(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        release = self._release(self._sale(self.fs), 300)
        with self.assertRaises(AccessError):
            release.with_user(self.user_accountant)._cl_override("Trusted farmer")
        with self.assertRaises(UserError):
            self.env['cl.rice.reason'].with_user(self.user_manager).create({
                'res_model': 'cl.rice.stock.release', 'res_id': release.id, 'action': 'override', 'reason': "  ",
                'date': '2026-11-15',
            }).action_confirm()
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.stock.release', 'res_id': release.id, 'action': 'override',
            'reason': "Buyer is the farmer's cooperative, pays next week", 'date': '2026-11-15',
        }).action_confirm()
        self.assertEqual(release.state, 'cleared')
        self.assertEqual(release.clearance_basis, 'override')
        self.assertEqual(release.cleared_uid, self.user_manager)
        self.assertEqual(release.balance_at_clearance, 350_000)
        self.assertTrue(any("pays next week" in m.body for m in self.fs.message_ids))
        release.action_release()
        self.assertEqual(self._released_qty(self.farmer)[0], 300)

    def test_cannot_release_more_than_held(self):
        sale = self._sale(self.fs, quantity=800)
        self._receive(self.fs, 50_000)
        with self.assertRaisesRegex(UserError, "only holds"):
            self._release(sale, 700).action_clear()
        first = self._release(sale, 400)
        first.action_clear()
        # the first cleared release already holds 400 of the 650 kg
        with self.assertRaisesRegex(UserError, "only holds"):
            self._release(sale, 300).action_clear()

    def test_partial_releases_check_the_balance_each_time(self):
        self._receive(self.fs, 50_000)
        sale = self._sale(self.fs, quantity=600)
        first = self._release(sale, 300)
        first.action_clear()
        first.action_release()
        self.assertEqual(sale.state, 'released')
        self.assertEqual(sale.remaining_qty, 300)
        # a new charge after the first release blocks the next one
        self._disburse(self._create_loan(self.fs, 100_000))
        second = self._release(sale, 300)
        with self.assertRaisesRegex(UserError, "Release blocked"):
            second.action_clear()
        self._receive(self.fs, 100_000)
        second.action_clear()
        second.action_release()
        self.assertEqual(sale.state, 'done')
        self.assertEqual(self._owned_qty(self.farmer, self.rice), 50)

    def test_gate_pass_only_for_cleared_release(self):
        self._receive(self.fs, 50_000)
        release = self._release(self._sale(self.fs), 200)
        with self.assertRaises(UserError):
            release.action_print_gate_pass()
        with self.assertRaises(UserError):
            self.env['ir.actions.report']._render_qweb_html('cl_rice_mill.report_gate_pass', release.ids)
        release.action_clear()
        html = self.env['ir.actions.report']._render_qweb_html('cl_rice_mill.report_gate_pass', release.ids)[0]
        self.assertIn(b'T 456 DEF', html)
        self.assertIn(b'Hamisi', html)
        self.assertIn(self.farmer.name.encode(), html)

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------
    def test_farmer_overpayment_is_held(self):
        payment = self._receive(self.fs, 80_000)
        self.assertEqual(payment.applied_amount, 50_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self.fs.funds_held, 30_000)
        self.assertEqual(self.fs.paid_total, 80_000)

    def test_payment_cancellation(self):
        payment = self._receive(self.fs, 50_000)
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.farmer.payment', 'res_id': payment.id, 'action': 'reverse', 'reason': "Fake slip",
        }).action_confirm()
        self.assertEqual(payment.state, 'cancelled')
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 50_000)

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------
    def _settle(self, fs, **vals):
        action = fs.with_user(self.user_accountant).action_open_settle()
        wizard = self.env['cl.rice.settlement.create'].with_user(self.user_accountant).with_context(
            action['context']).create({'payout_journal_id': self.cash_journal.id, **vals})
        result = wizard.action_create()
        settlement = self.env['cl.rice.settlement'].with_user(self.user_accountant).browse(result['res_id'])
        if settlement.settlement_type == 'final' and settlement.stock_held_kg:
            # these tests settle with rice still in store: a manager allows it
            settlement.with_user(self.user_manager)._cl_stock_override("Farmer keeps his rice for now")
        return settlement

    def test_settlement_with_funds_held(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        sale = self._sale(self.fs, route='luvanda')
        self._receive(self.fs, 1_000_000, 'buyer', sale_id=sale.id)
        settlement = self._settle(self.fs)
        self.assertEqual(settlement.owed_total, 350_000)
        self.assertEqual(settlement.earned_total, 1_000_000)
        self.assertEqual(settlement.net_amount, 650_000)
        open_lines = settlement.owed_line_ids | settlement.earned_line_ids
        self.assertEqual(set(open_lines.mapped('category')), {'loan', 'milling', 'funds_held'})
        settlement.action_confirm()
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.state, 'settled')
        self.assertEqual(self.fs.balance_owed, 0)
        settlement.action_pay()
        self.assertEqual(settlement.state, 'paid')
        self.assertEqual(settlement.with_user(self.env.user).payment_id.amount, 650_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.funds_held, 0)
        with self.assertRaises(UserError):
            self._create_loan(self.fs, 1000)

    def test_settlement_without_funds_carried_forward(self):
        self._disburse(self._create_loan(self.fs_2, 300_000))
        settlement = self._settle(self.fs_2, next_season_id=self.next_season.id)
        self.assertEqual(settlement.earned_total, 0)
        self.assertEqual(settlement.net_amount, -350_000)
        settlement.action_confirm()
        self.assertEqual(settlement.state, 'paid')
        carry = settlement.carry_loan_id
        self.assertEqual(carry.amount, 350_000)
        self.assertEqual(carry.loan_type, 'carry_forward')
        next_fs = carry.farmer_season_id
        self.assertEqual(next_fs.season_id, self.next_season)
        next_fs.invalidate_recordset()
        self.assertEqual(next_fs.balance_owed, 350_000)
        self.fs_2.invalidate_recordset()
        self.assertEqual(self.fs_2.balance_owed, 0)
        self.assertEqual(self.fs_2.state, 'settled')

    def test_settlement_negative_kept_receivable(self):
        self._disburse(self._create_loan(self.fs_2, 300_000))
        settlement = self._settle(self.fs_2, negative_policy='keep')
        settlement.action_confirm()
        self.assertFalse(settlement.carry_loan_id)
        self.fs_2.invalidate_recordset()
        self.assertEqual(self.fs_2.balance_owed, 350_000)
        self.assertTrue(self.fs_2.is_locked)

    def test_settlement_reversal(self):
        sale = self._sale(self.fs, route='luvanda')
        self._receive(self.fs, 400_000, 'buyer', sale_id=sale.id)
        settlement = self._settle(self.fs)
        settlement.action_confirm()
        settlement.action_pay()
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.settlement', 'res_id': settlement.id, 'action': 'reverse', 'reason': "Wrong payout",
        }).action_confirm()
        self.assertEqual(settlement.state, 'reversed')
        self.fs.invalidate_recordset()
        self.assertFalse(self.fs.is_locked)
        self.assertEqual(self.fs.balance_owed, 50_000)
        self.assertEqual(self.fs.funds_held, 400_000)

    # ------------------------------------------------------------------
    # The mill's own by-products are sold through normal sales
    # ------------------------------------------------------------------
    def test_byproduct_sale_is_mill_income(self):
        order = self.env['sale.order'].create({
            'partner_id': self.buyer.id,
            'warehouse_id': self.company.cl_rice_warehouse_id.id,
            'order_line': [(0, 0, {
                'product_id': self.bran.id, 'product_uom_qty': 150, 'price_unit': 300, 'tax_ids': [(6, 0, [])],
            })],
        })
        order.action_confirm()
        picking = order.picking_ids
        picking.action_assign()
        self.assertFalse(picking.move_line_ids.owner_id, "only the mill's own bran is delivered")
        self.assertEqual(sum(picking.move_line_ids.mapped('quantity')), 150)
        picking.button_validate()
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.invoice_line_ids.account_id, self.company.cl_rice_byproduct_income_account_id)
