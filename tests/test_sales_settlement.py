from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestSalesSettlement(ClRiceCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.paddy = cls.env.ref('cl_rice_mill.product_paddy')
        cls.rice = cls.env.ref('cl_rice_mill.product_rice')
        cls.w2 = cls.company.cl_rice_rice_warehouse_id
        cls.company.write({
            'cl_rice_bran_policy': 'kept',
            'cl_rice_bran_value': 100,
            'cl_rice_husk_policy': 'farmer',
            'cl_rice_broken_policy': 'farmer',
            'cl_rice_commission_type': 'percent',
            'cl_rice_commission_value': 5.0,
        })
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        cls.buyer = cls.env['res.partner'].create({'name': "Mbeya Rice Traders"})
        cls.next_season = cls.env['cl.rice.season'].create({
            'name': "Msimu 2027/28", 'code': '2728', 'date_start': '2027-07-01', 'date_end': '2028-06-30',
            'reference_price': 1000,
        })
        for fs in (cls.fs, cls.fs_2):
            cls._stock_rice(fs)

    @classmethod
    def _stock_rice(cls, fs):
        """1000 kg paddy in, 650 kg rice out (fee 50,000, bran credit 10,000)."""
        intake = cls.env['cl.rice.intake'].create({'farmer_season_id': fs.id, 'bags': 20, 'gross_weight': 1010})
        intake.action_confirm()
        job = cls.env['cl.rice.milling.job'].create({
            'farmer_season_id': fs.id,
            'input_lot_id': intake.lot_id.id,
            'input_kg': 1000, 'rice_kg': 650, 'rice_bags': 13, 'broken_kg': 50, 'bran_kg': 100, 'husk_kg': 180,
        })
        job.action_done()

    def _sale(self, lines, confirm=True):
        order = self.env['sale.order'].with_user(self.user_accountant).create({
            'partner_id': self.buyer.id,
            'warehouse_id': self.w2.id,
            'order_line': [(0, 0, {
                'product_id': self.rice.id,
                'product_uom_qty': qty,
                'price_unit': price,
                'tax_ids': [(6, 0, [])],
                'cl_farmer_season_id': fs.id,
                **extra,
            }) for fs, qty, price, extra in lines],
        })
        if confirm:
            order.action_confirm()
        return order

    def _deliver_and_invoice(self, order):
        picking = order.picking_ids
        picking.action_assign()
        picking.button_validate()
        self.assertEqual(picking.state, 'done')
        invoice = order._create_invoices()
        invoice.action_post()
        return invoice

    def _rice_owned(self, partner):
        quants = self.env['stock.quant'].search([
            ('product_id', '=', self.rice.id), ('owner_id', '=', partner.id), ('location_id.usage', '=', 'internal')])
        return sum(quants.mapped('quantity'))

    def _settle(self, fs, **vals):
        action = fs.with_user(self.user_accountant).action_open_settle()
        wizard = self.env['cl.rice.settlement.create'].with_user(self.user_accountant).with_context(
            action['context']).create({'payout_journal_id': self.cash_journal.id, **vals})
        result = wizard.action_create()
        return self.env['cl.rice.settlement'].with_user(self.user_accountant).browse(result['res_id'])

    # ------------------------------------------------------------------
    def test_agent_sale_combines_farmers(self):
        order = self._sale([(self.fs, 400, 2500, {}), (self.fs_2, 200, 2500, {})])
        moves = order.picking_ids.move_ids
        self.assertEqual(set(moves.mapped('restrict_partner_id').ids), {self.farmer.id, self.farmer_2.id})
        invoice = self._deliver_and_invoice(order)
        # each farmer's own lot left the store
        self.assertEqual(self._rice_owned(self.farmer), 250)
        self.assertEqual(self._rice_owned(self.farmer_2), 450)
        line_owners = order.picking_ids.move_line_ids.mapped(lambda l: (l.owner_id, l.lot_id.cl_farmer_season_id))
        self.assertIn((self.farmer, self.fs), line_owners)
        self.assertIn((self.farmer_2, self.fs_2), line_owners)
        # buyer invoice credits the clearing account, proceeds are split per line
        self.assertEqual(set(invoice.invoice_line_ids.account_id.ids), {self.company.cl_rice_clearing_account_id.id})
        proceeds = invoice.cl_proceeds_ids
        self.assertEqual(len(proceeds), 2)
        p1 = proceeds.filtered(lambda p: p.farmer_season_id == self.fs)
        self.assertEqual(p1.gross_amount, 1_000_000)
        self.assertEqual(p1.commission_amount, 50_000)
        self.assertEqual(p1.net_amount, 950_000)
        entry = p1.move_id
        payable = entry.line_ids.filtered(
            lambda l: l.account_id == self.company.cl_rice_proceeds_account_id and l.partner_id == self.farmer)
        self.assertEqual(payable.credit, 950_000)
        commission = entry.line_ids.filtered(lambda l: l.account_id == self.company.cl_rice_commission_income_account_id)
        self.assertEqual(sum(commission.mapped('credit')), 75_000)
        clearing = entry.line_ids.filtered(lambda l: l.account_id == self.company.cl_rice_clearing_account_id)
        self.assertEqual(sum(clearing.mapped('debit')), 1_500_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.state, 'sold')
        self.assertEqual(self.fs.credits_due, 960_000)  # proceeds + bran credit

    def test_cannot_sell_more_than_owned(self):
        order = self._sale([(self.fs, 700, 2500, {})], confirm=False)
        with self.assertRaises(ValidationError):
            order.action_confirm()

    def test_invoice_reset_reverses_proceeds(self):
        order = self._sale([(self.fs, 100, 2500, {})])
        invoice = self._deliver_and_invoice(order)
        self.assertTrue(invoice.cl_proceeds_ids)
        invoice.button_draft()
        self.assertFalse(invoice.cl_proceeds_ids)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.credits_due, 10_000)

    def test_agent_settlement_positive(self):
        self._disburse(self._create_loan(self.fs, 500_000))
        job = self.env['cl.rice.service.job'].create({
            'farmer_season_id': self.fs.id, 'service_type': 'ploughing', 'date': '2026-08-01', 'quantity': 4})
        job.action_done()
        job.action_charge()
        self._deliver_and_invoice(self._sale([(self.fs, 400, 2500, {})]))
        settlement = self._settle(self.fs)
        # owed: loan 500,000 + ploughing 200,000 + milling 50,000; earned: 950,000 + 10,000
        self.assertEqual(settlement.owed_total, 750_000)
        self.assertEqual(settlement.earned_total, 960_000)
        self.assertEqual(settlement.net_amount, 210_000)
        categories = set(settlement.line_ids.mapped('category'))
        self.assertEqual(categories, {'loan', 'service', 'milling', 'proceeds', 'byproduct'})
        settlement.action_confirm()
        self.assertEqual(settlement.state, 'confirmed')
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.state, 'settled')
        self.assertTrue(self.fs.is_locked)
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self.fs.credits_due, 210_000)
        self.assertEqual(set(self.fs.loan_ids.mapped('state')), {'settled'})
        settlement.action_pay()
        self.assertEqual(settlement.state, 'paid')
        self.assertEqual(settlement.with_user(self.env.user).payment_id.amount, 210_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.credits_due, 0)
        open_items = self.env['account.move.line'].search([
            ('cl_farmer_season_id', '=', self.fs.id),
            ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
            ('reconciled', '=', False), ('amount_residual', '!=', 0)])
        self.assertFalse(open_items)
        # the season is locked
        with self.assertRaises(UserError):
            self._create_loan(self.fs, 1000)
        with self.assertRaises(UserError):
            self.env['cl.rice.service.job'].create({
                'farmer_season_id': self.fs.id, 'service_type': 'ploughing', 'quantity': 1})

    def test_negative_balance_carried_forward(self):
        self._disburse(self._create_loan(self.fs_2, 900_000))
        self._deliver_and_invoice(self._sale([(self.fs_2, 200, 2500, {})]))
        settlement = self._settle(self.fs_2, next_season_id=self.next_season.id)
        # owed 900,000 + 50,000 milling; earned 475,000 + 10,000
        self.assertEqual(settlement.net_amount, -465_000)
        settlement.action_confirm()
        self.assertEqual(settlement.state, 'paid')
        carry = settlement.carry_loan_id
        self.assertEqual(carry.amount, 465_000)
        self.assertEqual(carry.loan_type, 'carry_forward')
        next_fs = carry.farmer_season_id
        self.assertEqual(next_fs.season_id, self.next_season)
        self.assertEqual(next_fs.partner_id, self.farmer_2)
        next_fs.invalidate_recordset()
        self.assertEqual(next_fs.balance_owed, 465_000)
        self.fs_2.invalidate_recordset()
        self.assertEqual(self.fs_2.balance_owed, 0)
        self.assertEqual(self.fs_2.state, 'settled')

    def test_negative_balance_kept_receivable(self):
        self._disburse(self._create_loan(self.fs_2, 900_000))
        self._deliver_and_invoice(self._sale([(self.fs_2, 200, 2500, {})]))
        settlement = self._settle(self.fs_2, negative_policy='keep')
        settlement.action_confirm()
        self.assertFalse(settlement.carry_loan_id)
        self.fs_2.invalidate_recordset()
        self.assertEqual(self.fs_2.balance_owed, 465_000)
        self.assertTrue(self.fs_2.is_locked)

    def test_settlement_reversal(self):
        self._disburse(self._create_loan(self.fs, 500_000))
        self._deliver_and_invoice(self._sale([(self.fs, 400, 2500, {})]))
        settlement = self._settle(self.fs)
        settlement.action_confirm()
        settlement.action_pay()
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.settlement',
            'res_id': settlement.id,
            'action': 'reverse',
            'reason': "Wrong sale price",
        }).action_confirm()
        self.assertEqual(settlement.state, 'reversed')
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.state, 'sold')
        self.assertEqual(self.fs.balance_owed, 550_000)
        self.assertEqual(self.fs.credits_due, 960_000)
        self.assertEqual(set(self.fs.loan_ids.mapped('state')), {'disbursed'})
        self.assertTrue(any("Wrong sale price" in m.body for m in self.fs.message_ids))

    def test_owner_mode_sale_and_settlement(self):
        self.company.cl_rice_sales_mode = 'owner'
        order = self._sale([(self.fs, 300, 2600, {'cl_farmer_price': 2000})])
        line = order.order_line
        transfer = line.cl_ownership_transfer_id
        self.assertEqual(transfer.state, 'done')
        self.assertEqual(transfer.amount, 600_000)
        bill = transfer.bill_id
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.amount_total, 600_000)
        # the bought rice is now the mill's, valued at the purchase price
        self.assertEqual(self._rice_owned(self.farmer), 350)
        mill_quants = self.env['stock.quant'].search([
            ('product_id', '=', self.rice.id), ('lot_id', '=', line.cl_lot_id.id),
            ('owner_id', '=', False), ('location_id.usage', '=', 'internal')])
        self.assertEqual(sum(mill_quants.mapped('quantity')), 300)
        self.assertAlmostEqual(sum(mill_quants.mapped('value')), 600_000)
        self.assertFalse(order.picking_ids.move_ids.restrict_partner_id)
        invoice = self._deliver_and_invoice(order)
        self.assertNotEqual(invoice.invoice_line_ids.account_id, self.company.cl_rice_clearing_account_id)
        self.assertFalse(invoice.cl_proceeds_ids)
        self.assertEqual(self._rice_owned(self.farmer), 350, "the farmer's remaining rice is untouched")
        settlement = self._settle(self.fs)
        purchase = settlement.line_ids.filtered(lambda l: l.category == 'purchase')
        self.assertEqual(purchase.amount, 600_000)
        # earned 600,000 + 10,000 bran; owed 50,000 milling
        self.assertEqual(settlement.net_amount, 560_000)
        settlement.action_confirm()
        settlement.action_pay()
        self.assertEqual(bill.payment_state, 'paid')
