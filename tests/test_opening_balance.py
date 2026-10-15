import base64

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ClRiceCommon

HEADERS = "Farmer Code,Farmer Name,Phone,Village,Loan,Tractor,Harvester,Operators Paid,Storage,Milling,Other," \
          "Paddy Bags,Paddy Kg,Rice Bags,Rice Kg"


@tagged('post_install', '-at_install')
class TestOpeningBalance(ClRiceCommon):

    def _batch(self, rows, headers=HEADERS):
        content = "\n".join([headers, *rows]).encode()
        return self.env['cl.rice.opening.balance'].with_user(self.user_manager).create({
            'season_id': self.season.id,
            'date': '2026-07-01',
            'file': base64.b64encode(content).decode(),
            'file_name': 'balances.csv',
        })

    def _rows(self):
        return [
            f"{self.farmer.cl_farmer_code},{self.farmer.name},,Igurusi,500000,100000,0,0,0,0,0,10,900,0,0",
            ",Asha Mwamba,255754000111,Ubaruku,0,0,0,40000,0,0,0,0,0,4,200",
        ]

    def test_valid_file_posts_debts_and_stock(self):
        batch = self._batch(self._rows())
        batch.action_import_file()
        self.assertEqual(batch.state, 'validated')
        self.assertEqual(batch.line_count, 2)
        self.assertEqual(batch.error_count, 0)
        self.assertEqual(batch.debt_total, 640_000)
        batch.action_post()
        self.assertEqual(batch.state, 'posted')
        # existing farmer: opening debts and paddy in his own lot
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 600_000)
        self.assertEqual(self.fs.opening_total, 600_000)
        self.assertEqual(self._owned_qty(self.farmer, self.paddy), 900)
        lot = self.fs._cl_find_lot(self.paddy)
        self.assertEqual(lot.cl_bag_qty, 10)
        quant = self.env['stock.quant'].search([('lot_id', '=', lot.id), ('quantity', '>', 0)])
        self.assertEqual(quant.location_id, self.company.cl_rice_paddy_location_id)
        self.assertEqual(quant.value, 0)
        loan_line = batch.line_ids[0].move_id.line_ids.filtered(lambda l: l.debit == 500_000)
        self.assertEqual(loan_line.account_id, self.company.cl_rice_loan_account_id)
        # new farmer created with his season
        line = batch.line_ids.filtered(lambda l: l.farmer_name == "Asha Mwamba")
        asha = line.partner_id
        self.assertTrue(asha.is_farmer)
        self.assertTrue(asha.cl_farmer_code)
        self.assertEqual(line.farmer_season_id.state, 'in_storage')
        self.assertEqual(line.farmer_season_id.balance_owed, 40_000)
        self.assertEqual(self._owned_qty(asha, self.rice), 200)
        quant = self.env['stock.quant'].search([('owner_id', '=', asha.id), ('quantity', '>', 0)])
        self.assertEqual(quant.location_id, self.company.cl_rice_rice_location_id)

    def test_swahili_headers_and_template(self):
        template = self.env['cl.rice.opening.balance']._cl_template_xlsx()
        rows = self.env['cl.rice.opening.balance']._cl_read_rows(template, 'template.xlsx')
        self.assertEqual(rows[0]['farmer_name'], "Juma Mwakalinga")
        self.assertEqual(rows[0]['loan_amount'], 500_000)
        swahili = "Namba ya Mkulima,Jina la Mkulima,Mkopo,Kilo za Mpunga"
        batch = self._batch([f"{self.farmer.cl_farmer_code},{self.farmer.name},70000,500"], headers=swahili)
        batch.action_import_file()
        self.assertEqual(batch.line_ids.loan_amount, 70_000)
        self.assertEqual(batch.line_ids.paddy_kg, 500)
        self.assertEqual(batch.line_ids.partner_id, self.farmer)

    def test_duplicates_are_rejected(self):
        rows = self._rows()
        batch = self._batch([rows[0], rows[0]])
        batch.action_import_file()
        self.assertEqual(batch.state, 'draft')
        self.assertEqual(batch.error_count, 1)
        self.assertIn("Duplicate", batch.line_ids[1].error)
        with self.assertRaises(UserError):
            batch.action_post()
        # once posted, the same farmer and season cannot be imported again
        first = self._batch([rows[0]])
        first.action_import_file()
        first.action_post()
        again = self._batch([rows[0]])
        again.action_import_file()
        self.assertEqual(again.error_count, 1)
        self.assertIn("already posted", again.line_ids.error)

    def test_invalid_rows(self):
        batch = self._batch([
            ",,,,100,0,0,0,0,0,0,0,0,0,0",
            f"{self.farmer.cl_farmer_code},{self.farmer.name},,,-5,0,0,0,0,0,0,0,0,0,0",
            f"{self.farmer.cl_farmer_code}X,,,,0,0,0,0,0,0,0,3,0,0,0",
        ])
        batch.action_import_file()
        self.assertEqual(batch.error_count, 3)

    def test_batch_reversal(self):
        batch = self._batch(self._rows())
        batch.action_import_file()
        batch.action_post()
        self.env['cl.rice.reason'].with_user(self.user_manager).create({
            'res_model': 'cl.rice.opening.balance', 'res_id': batch.id, 'action': 'reverse',
            'reason': "Wrong notebook figures",
        }).action_confirm()
        self.assertEqual(batch.state, 'reversed')
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)
        self.assertEqual(self._owned_qty(self.farmer, self.paddy), 0)
        asha = batch.line_ids.filtered(lambda l: l.farmer_name == "Asha Mwamba").partner_id
        self.assertEqual(self._owned_qty(asha, self.rice), 0)

    def test_only_manager_imports(self):
        batch = self._batch(self._rows())
        with self.assertRaises(Exception):
            batch.with_user(self.user_accountant).action_import_file()
