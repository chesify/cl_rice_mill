import uuid

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestMoneyProtection(ClRiceCommon):
    """Phase 1 of the fix round: release bypass (G1), stock held at settlement (G4), full season
    statement (G5), duplicate farmers (G7) and override follow-up (G8)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        cls.buyer = cls.env['res.partner'].create({'name': "Kyela Traders"})
        for fs in (cls.fs, cls.fs_2):
            cls._intake(fs)
            cls._mill(fs)  # 650 kg rice held, milling fee 50,000 owed

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _plain_delivery(self, lot, quantity, owner=None, product=None, location=None, user=None):
        """A plain outgoing transfer with a hand-made move line, as a user could do in Inventory."""
        warehouse = self.company.cl_rice_warehouse_id
        product = product or self.rice
        location = location or self.company.cl_rice_rice_location_id
        customers = self.env.ref('stock.stock_location_customers')
        Picking = self.env['stock.picking'].with_user(user) if user else self.env['stock.picking']
        picking = Picking.create({
            'picking_type_id': warehouse.out_type_id.id,
            'location_id': location.id,
            'location_dest_id': customers.id,
            'move_ids': [(0, 0, {
                'product_id': product.id, 'product_uom_qty': quantity, 'uom_id': product.uom_id.id,
                'location_id': location.id, 'location_dest_id': customers.id,
            })],
        })
        picking.action_confirm()
        picking.move_ids.move_line_ids.unlink()
        self.env['stock.move.line'].create({
            'move_id': picking.move_ids.id, 'picking_id': picking.id, 'product_id': product.id,
            'uom_id': product.uom_id.id, 'lot_id': lot.id, 'quantity': quantity,
            'location_id': location.id, 'location_dest_id': customers.id,
            'owner_id': owner.id if owner else False, 'picked': True,
        })
        return picking

    def _sale(self, fs, quantity=600, route='farmer'):
        return self.env['cl.rice.facilitated.sale'].create({
            'farmer_season_id': fs.id, 'buyer_id': self.buyer.id, 'product_id': self.rice.id,
            'quantity': quantity, 'price_unit': 2_500, 'payment_route': route,
        })

    def _release(self, sale, quantity):
        return self.env['cl.rice.stock.release'].create({'sale_id': sale.id, 'quantity': quantity})

    def _settlement(self, fs, settlement_type='final', **vals):
        action = fs.with_user(self.user_accountant).action_open_settle()
        wizard = self.env['cl.rice.settlement.create'].with_user(self.user_accountant).with_context(
            action['context']).create({'payout_journal_id': self.cash_journal.id, 'settlement_type': settlement_type,
                                       **vals})
        return self.env['cl.rice.settlement'].with_user(self.user_accountant).browse(wizard.action_create()['res_id'])

    # ------------------------------------------------------------------
    # G1: no farmer stock leaves through a plain delivery
    # ------------------------------------------------------------------
    def test_plain_delivery_of_farmer_stock_is_blocked(self):
        lot = self.fs._cl_find_lot(self.rice)
        picking = self._plain_delivery(lot, 500, owner=self.farmer)
        with self.assertRaisesRegex(UserError, "belongs to"):
            picking.button_validate()
        # same without the owner on the line: the lot still tells it is the farmer's
        picking = self._plain_delivery(lot, 100)
        with self.assertRaisesRegex(UserError, "belongs to"):
            picking.button_validate()
        self.assertEqual(self._owned_qty(self.farmer, self.rice), 650)

    def test_warehouse_officer_cannot_ship_from_rice_partitions(self):
        mill_lot = self.env['cl.rice.season']._cl_get_mill_lot(self.rice, self.season, self.company)
        self.env['stock.quant']._update_available_quantity(
            self.rice, self.company.cl_rice_rice_location_id, 40, lot_id=mill_lot)
        picking = self._plain_delivery(mill_lot, 40)
        with self.assertRaisesRegex(UserError, "Manager"):
            picking.with_user(self.user_warehouse).button_validate()
        # the mill's own bran leaves the by-products partition normally
        bran_lot = self.env['cl.rice.season']._cl_get_mill_lot(self.bran, self.season, self.company)
        bran = self._plain_delivery(bran_lot, 50, product=self.bran, location=self.company.cl_rice_byproduct_location_id)
        bran.with_user(self.user_warehouse).button_validate()
        self.assertEqual(bran.state, 'done')

    # ------------------------------------------------------------------
    # G4: stock held at settlement
    # ------------------------------------------------------------------
    def test_final_settlement_blocked_while_stock_is_held(self):
        settlement = self._settlement(self.fs, negative_policy='keep')
        self.assertEqual(settlement.stock_held_kg, 700)  # 650 kg rice and 50 kg broken rice
        with self.assertRaisesRegex(UserError, "still has 700"):
            settlement.action_confirm()
        with self.assertRaises(Exception):
            settlement._cl_stock_override("no right")
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.settlement', 'res_id': settlement.id, 'action': 'stock_override',
            'reason': "Farmer stores his rice with us after the season",
        }).action_confirm()
        settlement.action_confirm()
        self.assertEqual(settlement.stock_override_uid, self.user_manager)
        self.assertTrue(self.fs.is_locked)

    def test_interim_payout_keeps_the_season_open(self):
        sale = self._sale(self.fs, route='luvanda')
        self._receive(self.fs, 1_000_000, 'buyer', sale_id=sale.id)
        settlement = self._settlement(self.fs, 'interim')
        settlement.action_confirm()
        self.assertEqual(settlement.state, 'paid')
        # 50,000 milling paid from the money held, 950,000 paid out
        self.assertEqual(settlement.with_user(self.env.user).payment_id.amount, 950_000)
        self.fs.invalidate_recordset()
        self.assertFalse(self.fs.is_locked)
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self.fs.funds_held, 0)
        # nothing more to pay out
        with self.assertRaisesRegex(UserError, "Nothing can be paid out"):
            self._settlement(self.fs, 'interim').action_confirm()

    def test_interim_payout_never_pays_what_is_owed(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        sale = self._sale(self.fs, route='luvanda')
        self._receive(self.fs, 200_000, 'buyer', sale_id=sale.id)
        # by default nothing is paid out: all the money held goes to the debt
        settlement = self._settlement(self.fs, 'interim')
        self.assertEqual(settlement.payout_amount, 0)
        self.assertEqual(settlement.applied_amount, 200_000)
        self.assertFalse(settlement.needs_debt_approval)
        settlement.action_confirm()
        self.assertFalse(settlement.payment_id)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 150_000)
        self.assertEqual(self.fs.funds_held, 0)
        with self.assertRaisesRegex(UserError, "Nothing can be paid out"):
            self._settlement(self.fs, 'interim').action_confirm()

    # ------------------------------------------------------------------
    # N15: split of the money held between the farmer and the debt
    # ------------------------------------------------------------------
    def _owe_and_hold(self, held):
        """3,660,000 owed (3,610,000 loan and the 50,000 milling fee) and ``held`` received from a buyer."""
        self.fs.sudo().credit_limit_override = 10_000_000
        self._disburse(self._create_loan(self.fs, 3_610_000))
        sale = self.env['cl.rice.facilitated.sale'].create({
            'farmer_season_id': self.fs.id, 'buyer_id': self.buyer.id, 'product_id': self.rice.id,
            'quantity': 600, 'price_unit': 10_000, 'payment_route': 'luvanda'})
        self._receive(self.fs, held, 'buyer', sale_id=sale.id)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 3_660_000)
        self.assertEqual(self.fs.funds_held, held)

    def test_partial_payout_while_owing_needs_manager(self):
        self._owe_and_hold(2_000_000)
        settlement = self._settlement(self.fs, 'interim', payout_amount=500_000)
        self.assertEqual(settlement.funds_held_amount, 2_000_000)
        self.assertEqual(settlement.applied_amount, 1_500_000)
        self.assertEqual(settlement.owed_after, 2_160_000)
        self.assertEqual(settlement.kept_amount, 0)
        self.assertTrue(settlement.needs_debt_approval)
        # 650 kg rice at 2,000 against 2,160,000 still owed
        self.assertAlmostEqual(settlement.cover_after, 1_300_000 / 2_160_000 * 100, places=1)
        with self.assertRaisesRegex(UserError, "Manager must approve"):
            settlement.action_confirm()
        with self.assertRaises(Exception):
            settlement.with_user(self.user_accountant)._cl_payout_with_debt("no right")
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.settlement', 'res_id': settlement.id, 'action': 'payout_with_debt',
            'reason': "School fees, the rice in store covers the rest",
        }).action_confirm()
        self.assertEqual(settlement.debt_approval_uid, self.user_manager)
        settlement.action_confirm()
        self.assertEqual(settlement.state, 'paid')
        self.assertEqual(settlement.with_user(self.env.user).payment_id.amount, 500_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 2_160_000)
        self.assertEqual(self.fs.funds_held, 0)
        self.assertFalse(self.fs.is_locked)
        # both parts are on the farmer's statement
        payouts = self.fs._cl_statement()['payouts']
        self.assertEqual([(p['applied'], p['paid']) for p in payouts], [(1_500_000, 500_000)])
        html = self.env['ir.actions.report']._render_qweb_html(
            'cl_rice_mill.action_report_settlement_statement', settlement.ids)[0].decode()
        self.assertIn("Applied to your balance", html)
        self.assertIn("Paid to you", html)

    def test_changing_the_amount_drops_the_approval(self):
        self._owe_and_hold(2_000_000)
        settlement = self._settlement(self.fs, 'interim', payout_amount=500_000)
        settlement.with_user(self.user_manager)._cl_payout_with_debt("Approved for 500,000")
        settlement.payout_amount = 900_000
        self.assertFalse(settlement.debt_approval_reason)
        self.assertEqual(settlement.applied_amount, 1_100_000)
        with self.assertRaisesRegex(UserError, "Manager must approve"):
            settlement.action_confirm()

    def test_payout_cannot_exceed_funds_held(self):
        self._owe_and_hold(2_000_000)
        with self.assertRaisesRegex(UserError, "at most"):
            self._settlement(self.fs, 'interim', payout_amount=2_500_000)

    def test_default_split_pays_the_rest(self):
        self._owe_and_hold(5_000_000)
        settlement = self._settlement(self.fs, 'interim')
        self.assertEqual(settlement.payout_amount, 1_340_000)
        self.assertEqual(settlement.applied_amount, 3_660_000)
        self.assertEqual(settlement.owed_after, 0)
        self.assertFalse(settlement.needs_debt_approval)
        settlement.action_confirm()
        self.assertEqual(settlement.with_user(self.env.user).payment_id.amount, 1_340_000)
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self.fs.funds_held, 0)

    def test_money_not_paid_nor_applied_stays_held(self):
        self._owe_and_hold(5_000_000)
        settlement = self._settlement(self.fs, 'interim', payout_amount=1_000_000)
        self.assertEqual(settlement.applied_amount, 3_660_000)
        self.assertEqual(settlement.kept_amount, 340_000)
        settlement.action_confirm()
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self.fs.funds_held, 340_000)

    def test_release_after_zero_balance_settlement(self):
        self._receive(self.fs, 50_000)
        settlement = self._settlement(self.fs)
        settlement.with_user(self.user_manager)._cl_stock_override("Rice sold later")
        settlement.action_confirm()
        self.assertTrue(self.fs.is_locked)
        release = self._release(self._sale(self.fs, quantity=650), 650)
        release.action_clear()
        release.action_release()
        self.assertEqual(self._owned_qty(self.farmer, self.rice), 0)
        self.assertFalse(settlement.move_id, "nothing was open: no settlement entry")
        self.assertEqual(self.fs.state, 'settled')
        # no new charge on the settled season
        with self.assertRaises(UserError):
            self._create_loan(self.fs, 1000)

    def test_no_release_after_settlement_with_balance(self):
        self._disburse(self._create_loan(self.fs_2, 300_000))
        settlement = self._settlement(self.fs_2, negative_policy='keep')
        settlement.with_user(self.user_manager)._cl_stock_override("Keeps rice")
        settlement.action_confirm()
        with self.assertRaisesRegex(UserError, "balance still owed"):
            self._sale(self.fs_2)

    # ------------------------------------------------------------------
    # G5: the settlement lists the whole season
    # ------------------------------------------------------------------
    def test_settlement_shows_full_breakdown(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        sale = self._sale(self.fs, route='luvanda')
        self._receive(self.fs, 1_000_000, 'buyer', sale_id=sale.id)
        release = self._release(sale, 300)
        release.action_clear()  # the buyer's money pays the 350,000 owed first
        self.assertEqual(release.funds_applied, 350_000)
        settlement = self._settlement(self.fs)
        statement = settlement.statement_line_ids
        self.assertEqual(sorted(statement.mapped('category')), ['buyer_payment', 'loan', 'milling'])
        self.assertEqual(settlement.statement_charges, 350_000)
        self.assertEqual(settlement.statement_receipts, 1_000_000)
        self.assertIn("land preparation", statement.filtered(lambda l: l.category == 'loan').name)
        # the open items only show the leftover money held, the statement explains it
        self.assertEqual(settlement.earned_total, 650_000)
        self.assertEqual(settlement.owed_total, 0)
        self.assertEqual(settlement.statement_receipts - settlement.statement_charges, settlement.net_amount)

    # ------------------------------------------------------------------
    # G7: duplicate farmers
    # ------------------------------------------------------------------
    def test_duplicate_nida_rejected(self):
        self.farmer.cl_nida_number = '19800101-12345-00001-12'
        with mute_logger('odoo.sql_db'), self.assertRaises((ValidationError, IntegrityError)):
            self.env['res.partner'].create({
                'name': "Amosi Kalinga", 'is_farmer': True, 'cl_nida_number': '1980010112345 0000112'})

    def test_duplicate_phone_is_flagged(self):
        self.farmer.phone = '0712 345 678'
        twin = self.env['res.partner'].create({'name': "Juma M.", 'is_farmer': True, 'phone': '+255712345678'})
        self.assertEqual(twin.cl_possible_duplicate_ids, self.farmer)
        [result] = self.env['cl.rice.field.sync'].with_user(self.user_officer)._cl_sync([{
            'uuid': str(uuid.uuid4()), 'kind': 'farmer', 'data': {'name': "Juma Again", 'phone': '712345678'}}])
        registered = self.env['res.partner'].browse(result['id'])
        self.assertEqual(registered.cl_farmer_state, 'pending')
        self.assertIn("Possible duplicate", registered.comment)
        self.assertIn(self.farmer, registered.cl_possible_duplicate_ids)

    def test_opening_balance_rejects_duplicate_phone(self):
        self.farmer.phone = '0712 345 678'
        batch = self.env['cl.rice.opening.balance'].create({
            'season_id': self.season.id,
            'line_ids': [(0, 0, {'farmer_name': "Juma Mwakalinga II", 'phone': '0712345678', 'loan_amount': 1000})],
        })
        batch.action_validate()
        self.assertIn("Phone already used", batch.line_ids.error)

    # ------------------------------------------------------------------
    # G8: an override is followed up
    # ------------------------------------------------------------------
    def test_override_needs_promised_date_and_creates_follow_up(self):
        self._disburse(self._create_loan(self.fs, 300_000))
        release = self._release(self._sale(self.fs), 300)
        values = {'res_model': 'cl.rice.stock.release', 'res_id': release.id, 'action': 'override',
                  'reason': "Pays after selling to his cooperative"}
        with self.assertRaisesRegex(UserError, "promised"):
            self.env['cl.rice.reason'].with_user(self.user_manager).create(values).action_confirm()
        promised = fields.Date.to_date('2026-11-20')
        self.env['cl.rice.reason'].with_user(self.user_manager).create(
            dict(values, date=promised)).action_confirm()
        self.assertEqual(release.override_promised_date, promised)
        activity = self.fs.activity_ids.filtered(lambda a: "promised" in (a.note or ''))
        self.assertEqual(activity.date_deadline, promised)
        self.assertEqual(activity.user_id, self.user_accountant)
        self.fs.invalidate_recordset()
        self.assertTrue(self.fs.override_outstanding)
        self.assertEqual(self.fs.override_promised_date, promised)
        self.assertIn(self.fs, self.env['cl.rice.farmer.season'].search([('override_outstanding', '=', True)]))
        self._receive(self.fs, 350_000)
        self.fs.invalidate_recordset()
        self.assertFalse(self.fs.override_outstanding)
