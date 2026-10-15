import json
import uuid

from odoo.tests import HttpCase, new_test_user, tagged

from .common import ClRiceCommon


@tagged('post_install', '-at_install')
class TestFieldSync(ClRiceCommon):

    def _job(self, fs=None, **data):
        return {
            'uuid': str(uuid.uuid4()),
            'kind': 'service_job',
            'data': {
                'farmer_season_id': (fs or self.fs).id,
                'service_type': 'ploughing',
                'vehicle_id': self.tractor.id,
                'operator_id': self.operator.id,
                'quantity': '3.5',
                'fuel_litres': '12',
                'date': '2026-08-02',
                **data,
            },
        }

    def _sync(self, records, user=None):
        return self.env['cl.rice.field.sync'].with_user(user or self.user_officer)._cl_sync(records)

    def test_service_jobs_arrive_as_drafts(self):
        record = self._job(photo="data:image/jpeg;base64,/9j/4AAQSkZJRg==")
        [result] = self._sync([record])
        self.assertEqual(result['status'], 'synced')
        job = self.env['cl.rice.service.job'].browse(result['id'])
        self.assertEqual(job.state, 'draft')
        self.assertEqual(job.source, 'field')
        self.assertEqual(job.quantity, 3.5)
        self.assertEqual(job.vehicle_id, self.tractor)
        self.assertEqual(job.cl_client_uuid, record['uuid'])
        self.assertTrue(job.photo)
        self.assertFalse(job.invoice_id, "field records never charge the farmer")
        self.fs.invalidate_recordset()
        self.assertEqual(self.fs.balance_owed, 0)

    def test_duplicate_uuid_is_ignored(self):
        record = self._job()
        first = self._sync([record])
        second = self._sync([record])
        both = self._sync([self._job(), record])
        self.assertEqual(first[0]['status'], 'synced')
        self.assertEqual(second[0]['status'], 'duplicate')
        self.assertEqual(second[0]['id'], first[0]['id'])
        self.assertEqual([r['status'] for r in both], ['synced', 'duplicate'])
        self.assertEqual(self.env['cl.rice.service.job'].search_count([('cl_client_uuid', '=', record['uuid'])]), 1)

    def test_officer_syncs_only_own_farmers(self):
        [result] = self._sync([self._job(fs=self.fs_2)])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.env['cl.rice.service.job'].search([('farmer_season_id', '=', self.fs_2.id)]))
        # the other officer can
        [result] = self._sync([self._job(fs=self.fs_2)], user=self.user_officer_2)
        self.assertEqual(result['status'], 'synced')

    def test_farmer_registration(self):
        record = {'uuid': str(uuid.uuid4()), 'kind': 'farmer',
                  'data': {'name': "Zawadi Mwakyusa", 'phone': '255765000222', 'village': "Igurusi",
                           'acreage': '2.5', 'latitude': '-8.9123456', 'longitude': '33.4612345'}}
        [result] = self._sync([record])
        self.assertEqual(result['status'], 'synced')
        farmer = self.env['res.partner'].browse(result['id'])
        self.assertTrue(farmer.is_farmer)
        self.assertEqual(farmer.cl_farmer_state, 'pending')
        self.assertEqual(farmer.cl_field_officer_id, self.user_officer)
        self.assertAlmostEqual(farmer.cl_farm_latitude, -8.9123456)
        self.assertEqual(self._sync([record])[0]['status'], 'duplicate')

    def test_invalid_records(self):
        results = self._sync([
            {'uuid': 'x', 'kind': 'service_job', 'data': {}},
            {'uuid': str(uuid.uuid4()), 'kind': 'payment', 'data': {}},
            {'uuid': str(uuid.uuid4()), 'kind': 'farmer', 'data': {'name': ''}},
            self._job(service_type='milling'),
        ])
        self.assertEqual([r['status'] for r in results], ['failed'] * 4)

    def test_bootstrap_lists_only_own_farmers(self):
        data = self.env['cl.rice.field.sync'].with_user(self.user_officer)._cl_bootstrap()
        self.assertEqual([fs['id'] for fs in data['farmer_seasons']], [self.fs.id])
        self.assertIn('ploughing', [s['id'] for s in data['services']])
        self.assertIn(self.tractor.id, [m['id'] for m in data['machines']])


@tagged('post_install', '-at_install')
class TestFieldSyncHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.officer = new_test_user(
            cls.env, login='rm_http_officer', password='rm_http_officer',
            group_ids=[cls.env.ref('cl_rice_mill.cl_rice_mill_group_field_officer').id])
        season = cls.env['cl.rice.season'].create({
            'name': "Season HTTP", 'code': 'HTTP', 'date_start': '2026-07-01', 'date_end': '2027-06-30'})
        farmer = cls.env['res.partner'].create({
            'name': "Http Farmer", 'is_farmer': True, 'cl_field_officer_id': cls.officer.id})
        cls.fs = cls.env['cl.rice.farmer.season'].create({'partner_id': farmer.id, 'season_id': season.id})
        cls.fs.action_activate()

    def _rpc(self, path, params):
        response = self.url_open(path, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
                                 headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        return response.json()['result']

    def test_sync_endpoint_is_idempotent(self):
        self.authenticate('rm_http_officer', 'rm_http_officer')
        record = {'uuid': str(uuid.uuid4()), 'kind': 'service_job',
                  'data': {'farmer_season_id': self.fs.id, 'service_type': 'harrowing', 'quantity': 2}}
        first = self._rpc('/rice/field/sync', {'records': [record]})
        second = self._rpc('/rice/field/sync', {'records': [record]})
        self.assertEqual(first[0]['status'], 'synced')
        self.assertEqual(second[0]['status'], 'duplicate')
        jobs = self.env['cl.rice.service.job'].search([('cl_client_uuid', '=', record['uuid'])])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.state, 'draft')
        data = self._rpc('/rice/field/data', {})
        self.assertEqual([fs['id'] for fs in data['farmer_seasons']], [self.fs.id])

    def test_app_shell(self):
        self.authenticate('rm_http_officer', 'rm_http_officer')
        page = self.url_open('/rice/field/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('field_app.js', page.text)
        worker = self.url_open('/rice/field/sw.js')
        self.assertEqual(worker.headers.get('Service-Worker-Allowed'), '/rice/field/')
        manifest = self.url_open('/rice/field/manifest.webmanifest').json()
        self.assertEqual(manifest['scope'], '/rice/field/')
