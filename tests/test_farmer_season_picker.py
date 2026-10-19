from odoo.tests import HttpCase, tagged

from .test_dashboard import WAIT_JS


@tagged('post_install', '-at_install')
class TestFarmerSeasonPicker(HttpCase):
    """The farmer season dropdown on the operation forms lists the open farmer seasons."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company._cl_rice_setup_company()
        season = cls.env['cl.rice.season'].create({
            'name': "Season Picker", 'code': 'PK', 'date_start': '2026-07-01', 'date_end': '2027-06-30',
            'reference_price': 900})
        season.action_activate()
        farmer = cls.env['res.partner'].create({'name': "Picker Farmer", 'is_farmer': True})
        cls.fs = cls.env['cl.rice.farmer.season'].create({'partner_id': farmer.id, 'season_id': season.id, 'acreage': 3})
        cls.fs.action_activate()

    def _check_picker(self, action, typed=''):
        code = WAIT_JS + """
            (async () => {
                const input = await wait('.o_field_widget[name="farmer_season_id"] input');
                input.focus();
                input.click();
                input.value = %r;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                await wait('.o-autocomplete--dropdown-menu');
                for (let i = 0; i < 50; i++) {
                    const items = [...document.querySelectorAll('.o-autocomplete--dropdown-item')].map((li) => li.textContent);
                    if (items.some((text) => text.includes('Picker Farmer'))) {
                        console.log('test successful');
                        return;
                    }
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                const items = [...document.querySelectorAll('.o-autocomplete--dropdown-item')].map((li) => li.textContent);
                throw new Error('farmer season not offered: ' + items.join(' | '));
            })().catch((error) => console.error(error.message));
        """ % typed
        self.browser_js(f'/odoo/action-cl_rice_mill.{action}/new', code, login='admin', timeout=120)

    def test_service_job_picker(self):
        self._check_picker('cl_rice_service_job_action')
        self._check_picker('cl_rice_service_job_action', 'Picker')

    def test_loan_picker(self):
        self._check_picker('cl_rice_loan_action', 'Picker')

    def test_search_by_farmer(self):
        FS = self.env['cl.rice.farmer.season']
        self.assertIn(self.fs.id, [r[0] for r in FS.name_search('Picker Farm')])
        code = self.fs.partner_id.cl_farmer_code
        if code:
            self.assertIn(self.fs.id, [r[0] for r in FS.name_search(code)])
