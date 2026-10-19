import base64
import time

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestDashboard(ClRiceCommon):
    """Section 3: the management dashboard returns the right totals, per role, quickly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.cl_rice_fuel_price = 3_000
        cls.rate_ploughing.operator_rate = 5_000
        cls.env['cl.rice.service.rate'].create({
            'service_type': 'milling', 'uom': 'kg', 'price_farmer': 50, 'date_from': '2026-01-01'})
        cls.mill = cls.env['fleet.vehicle'].create({
            'model_id': cls.tractor.model_id.id, 'license_plate': 'MILL 1', 'cl_machine_category': 'mill'})
        cls.loan = cls._disburse(cls._create_loan(cls.fs, 300_000))
        cls.job = cls.env['cl.rice.service.job'].create({
            'farmer_season_id': cls.fs.id, 'service_type': 'ploughing', 'date': '2026-08-01',
            'vehicle_id': cls.tractor.id, 'operator_id': cls.operator.id, 'quantity': 4, 'fuel_litres': 20})
        cls.job.action_done()
        cls.job.action_charge()
        cls._intake(cls.fs)
        cls.milling = cls._mill(cls.fs, vehicle_id=cls.mill.id)
        cls._receive(cls.fs, 100_000)
        cls._intake(cls.fs_2)
        cls.Dashboard = cls.env['cl.rice.dashboard']
        cls.filters = {'season_id': cls.season.id}

    def _data(self, section, user=None, **filters):
        Dashboard = self.Dashboard.with_user(user) if user else self.Dashboard
        return Dashboard.get_dashboard_data(section, dict(self.filters, **filters), force=True)

    def _kpis(self, user=None, **filters):
        return {kpi['key']: kpi for kpi in self._data('kpi', user, **filters)['kpis']}

    def test_kpi_totals(self):
        kpis = self._kpis()
        self.assertEqual(kpis['income']['value'], 250_000)  # ploughing 200,000 + milling 50,000
        self.assertEqual(kpis['profit']['value'], 250_000 - 60_000 - 20_000)  # fuel and operator wages
        self.assertEqual(kpis['loans']['value'], 300_000)
        self.assertEqual(kpis['owed']['value'] - kpis['funds']['value'], 550_000 - 100_000)
        self.assertEqual(kpis['paddy']['value'], 1000)  # two intakes of 1,000 kg net, 1,000 kg milled
        self.assertEqual(kpis['rice']['value'], 650 + 50)
        self.assertEqual(kpis['active']['value'], 2)
        self.assertIn(kpis['cover']['band'], ('red', 'amber', 'green'))
        self.assertTrue(kpis['income']['action']['domain'])
        self.assertEqual(len(kpis['income']['spark']), 6)

    def test_sections(self):
        income = self._data('income')
        self.assertEqual(income['totals']['income'], 250_000)
        self.assertEqual(income['totals']['costs'], 80_000)
        self.assertEqual(income['waterfall'][-1]['value'], 170_000)
        farmers = self._data('farmers')
        self.assertEqual(farmers['top_owed'][0]['id'], self.fs.id)
        self.assertEqual(sum(row['value'] for row in farmers['ageing']), farmers['top_owed'][0]['owed'])
        self.assertEqual(sum(row['value'] for row in farmers['pipeline']), 2)
        stock = self._data('stock')
        self.assertEqual(sum(stock['partitions']['farmer']), 1000 + 650 + 50)
        self.assertEqual(sum(stock['flows']['intake']), 2000)
        milling = self._data('milling')
        self.assertEqual(milling['recovery_machine'][0]['recovery'], 65.0)
        self.assertEqual(milling['modes'][0]['income'], 50_000)
        machines = {row['id']: row for row in self._data('machines')['machines']}
        tractor = machines[self.tractor.id]
        self.assertEqual((tractor['income'], tractor['fuel'], tractor['wages']), (200_000, 60_000, 20_000))
        self.assertEqual(tractor['net'], 120_000)
        self.assertEqual(tractor['work'], 4)
        self.assertEqual(machines[self.mill.id]['income'], 50_000)
        field = self._data('field')
        self.assertEqual(sum(row['count'] for row in field['per_officer']), 1)
        alerts = self._data('alerts')['alerts']
        self.assertTrue(all(alert['action'] for alert in alerts))

    def test_filters(self):
        self.assertEqual(self._kpis(vehicle_id=self.tractor.id)['income']['value'], 200_000)
        self.assertEqual(self._kpis(service_type='milling')['income']['value'], 50_000)
        self.assertEqual(self._kpis(village="Ubaruku")['income']['value'], 0)
        self.assertEqual(self._kpis(date_from='2026-07-01', date_to='2026-08-31')['income']['value'], 200_000)
        compare = self._data('income', compare=True)
        self.assertIn('previous', compare['profit_trend'])

    def test_roles(self):
        self.assertEqual(self.Dashboard.with_user(self.user_officer).get_dashboard_meta()['sections'],
                         ['farmers', 'field'])
        with self.assertRaises(AccessError):
            self._data('income', self.user_officer)
        mine = self._data('farmers', self.user_officer)
        self.assertEqual([row['id'] for row in mine['top_owed']], [self.fs.id])
        other = self._data('farmers', self.user_officer_2)
        self.assertNotIn(self.fs.id, [row['id'] for row in other['top_owed']])
        self.assertEqual(sum(row['value'] for row in other['pipeline']), 1, "only the officer's own farmers")
        warehouse = self.Dashboard.with_user(self.user_warehouse)
        self.assertEqual(warehouse.get_dashboard_meta()['sections'], ['kpi', 'stock', 'milling'])
        self.assertEqual({k['key'] for k in self._data('kpi', self.user_warehouse)['kpis']}, {'paddy', 'rice', 'active'})
        accountant = self.Dashboard.with_user(self.user_accountant).get_dashboard_meta()
        self.assertEqual(accountant['sections'], ['kpi', 'income', 'farmers', 'alerts'])

    def test_cache_and_saved_filters(self):
        first = self.Dashboard.get_dashboard_data('kpi', self.filters, force=True)
        again = self.Dashboard.get_dashboard_data('kpi', self.filters)
        self.assertTrue(again['cached'])
        self.assertEqual(first['updated'], again['updated'])
        self.Dashboard.with_user(self.user_manager).save_dashboard_filters({'season_id': self.season.id, 'village': 'X'})
        meta = self.Dashboard.with_user(self.user_manager).get_dashboard_meta()
        self.assertEqual(meta['saved_filters']['village'], 'X')

    def test_export_and_panel(self):
        content = self.Dashboard.export_dashboard_xlsx("Machines", ["Machine", "Net"], [["Tractor", 120000.0]])
        self.assertEqual(base64.b64decode(content)[:2], b'PK')
        panel = self.Dashboard.with_user(self.user_officer).get_farmer_season_panel(self.fs.id)
        self.assertEqual(panel['stock_kg'], 650 + 50)
        self.assertTrue(panel['charges'])
        self.assertTrue(panel['timeline'])
        with self.assertRaises(AccessError):
            self.Dashboard.with_user(self.user_officer).get_farmer_season_panel(self.fs_2.id)

    def test_performance_300_farmers(self):
        season = self.Dashboard.with_user(self.user_manager)._cl_generate_demo(farmers=300)
        self.assertEqual(season.farmer_count, 300)
        filters = {'season_id': season.id}
        for section in ('kpi', 'income', 'farmers', 'stock', 'milling', 'machines', 'field', 'alerts'):
            start = time.time()
            self.Dashboard.with_user(self.user_manager).get_dashboard_data(section, filters, force=True)
            self.assertLess(time.time() - start, 2.0, f"section {section} is too slow")


WAIT_JS = """
const wait = async (selector) => {
    for (let i = 0; i < 300; i++) {
        const element = document.querySelector(selector);
        if (element) { return element; }
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`${selector} was not rendered`);
};
"""


@tagged('post_install', '-at_install')
class TestDashboardUi(HttpCase):
    """The dashboard and the farmer season panel render in the browser without errors."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company._cl_rice_setup_company()
        season = cls.env['cl.rice.season'].create({
            'name': "Season UI", 'code': 'UI', 'date_start': '2026-07-01', 'date_end': '2027-06-30',
            'reference_price': 900})
        season.action_activate()
        farmer = cls.env['res.partner'].create({'name': "UI Farmer", 'is_farmer': True})
        cls.fs = cls.env['cl.rice.farmer.season'].create({'partner_id': farmer.id, 'season_id': season.id, 'acreage': 3})
        cls.fs.action_activate()
        intake = cls.env['cl.rice.intake'].create({'farmer_season_id': cls.fs.id, 'bags': 10, 'gross_weight': 905})
        intake.action_confirm()
        cls.intake = intake

    def _mill(self):
        brand = self.env['fleet.vehicle.model.brand'].create({'name': "Satake"})
        model = self.env['fleet.vehicle.model'].create({'name': "Mill 1", 'brand_id': brand.id, 'vehicle_type': 'car'})
        mill = self.env['fleet.vehicle'].create({'model_id': model.id, 'license_plate': 'MILL UI',
                                                 'cl_machine_category': 'mill'})
        job = self.env['cl.rice.milling.job'].create({
            'farmer_season_id': self.fs.id, 'input_lot_id': self.intake.lot_id.id, 'vehicle_id': mill.id,
            'input_kg': 400, 'rice_kg': 260, 'husk_kg': 100,
            'date_start': '2026-10-10 07:00:00', 'date_end': '2026-10-10 10:00:00'})
        job.action_done()

    def test_dashboard_renders(self):
        self._mill()
        code = WAIT_JS + """
            (async () => {
                await wait('.o_cl_rice_filters select');
                await wait('.o_cl_rice_kpi .o_cl_rice_kpi_value');
                await wait('.o_cl_rice_section_title');
                await wait('.o_cl_rice_chart canvas');
                await wait('.o_cl_rice_alerts');
                await wait('.o_cl_rice_dashboard .progress-bar');
                const titles = [...document.querySelectorAll('.o_cl_rice_section_title')].map((h) => h.textContent);
                if (titles.length < 6) { throw new Error('sections missing: ' + titles.join(', ')); }
                document.querySelector('.o_cl_rice_kpi').click();
                await wait('.o_list_view');
                console.log('test successful');
            })().catch((error) => console.error(error.message));
        """
        self.browser_js('/odoo/action-cl_rice_mill.cl_rice_dashboard_action', code, login='admin', timeout=120)

    def test_farmer_season_panel_renders(self):
        code = WAIT_JS + """
            (async () => {
                await wait('.o_form_view .o_notebook');
                const tab = [...document.querySelectorAll('.o_notebook .nav-link')].find(
                    (link) => link.textContent.trim() === 'Overview');
                if (!tab) {
                    throw new Error('no Overview tab: ' + document.querySelector('.o_notebook').textContent.slice(0, 200));
                }
                tab.click();
                await wait('.o_cl_rice_fs_panel .fs-5');
                const text = document.querySelector('.o_cl_rice_fs_panel').textContent;
                if (!text.includes('kg in store')) { throw new Error('stock missing: ' + text); }
                console.log('test successful');
            })().catch((error) => console.error(error.message));
        """
        self.browser_js(f'/odoo/action-cl_rice_mill.cl_rice_farmer_season_action/{self.fs.id}', code, login='admin',
                        timeout=120)
