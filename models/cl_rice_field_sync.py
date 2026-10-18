import re

from psycopg2 import IntegrityError

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

UUID_PATTERN = re.compile(r'^[0-9a-fA-F-]{16,64}$')
MAX_RECORDS = 200
JOB_SERVICE_TYPES = ('ploughing', 'harrowing', 'harvesting', 'transport', 'bagging', 'other')


class ClRiceFieldSync(models.AbstractModel):
    _name = 'cl.rice.field.sync'
    _description = "Field App Synchronisation"

    # ------------------------------------------------------------------
    # Data the app caches for offline use
    # ------------------------------------------------------------------
    @api.model
    def _cl_bootstrap(self):
        """What the field app needs offline: only the farmers assigned to this officer."""
        user = self.env.user
        seasons = self.env['cl.rice.farmer.season'].search([
            ('field_officer_id', '=', user.id),
            ('state', 'in', ('active', 'harvested', 'in_storage', 'released')),
        ])
        Vehicle = self.env['fleet.vehicle']
        machines = Vehicle.search([('cl_machine_category', 'in', ('tractor', 'harvester'))])
        vehicles = Vehicle.search([('cl_machine_category', 'in', ('motorcycle', 'car', 'truck'))])
        operators = self.env['hr.employee.public'].search([('company_id', 'in', self.env.companies.ids)])
        types = dict(self.env['cl.rice.service.job']._fields['service_type']._description_selection(self.env))
        return {
            'user': user.name,
            'farmer_seasons': [{
                'id': fs.id,
                'name': fs.partner_id.name,
                'code': fs.partner_id.cl_farmer_code or '',
                'village': fs.partner_id.cl_village or '',
                'season': fs.season_id.name,
            } for fs in seasons],
            'services': [{'id': key, 'name': label} for key, label in types.items()],
            'machines': [{'id': machine.id, 'name': machine.display_name} for machine in machines],
            'vehicles': [{'id': vehicle.id, 'name': vehicle.display_name} for vehicle in vehicles],
            'operators': [{'id': employee.id, 'name': employee.name} for employee in operators],
        }

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------
    @api.model
    def _cl_sync(self, records):
        """Create the records captured offline, as drafts, under the officer's own rights.

        Every record carries a client-generated ``uuid``: a record already received is reported
        as ``duplicate`` and never created twice.

        :param records: list of ``{'uuid': str, 'kind': 'service_job' | 'farmer', 'data': dict}``
        :return: list of ``{'uuid', 'status': 'synced' | 'duplicate' | 'failed', 'id', 'error'}``
        """
        if not isinstance(records, list):
            raise UserError(self.env._("Invalid synchronisation data."))
        results = []
        for record in records[:MAX_RECORDS]:
            results.append(self._cl_sync_one(record))
        return results

    @api.model
    def _cl_sync_one(self, record):
        uuid = isinstance(record, dict) and record.get('uuid')
        if not uuid or not isinstance(uuid, str) or not UUID_PATTERN.match(uuid):
            return {'uuid': uuid if isinstance(uuid, str) else False, 'status': 'failed',
                    'error': self.env._("Missing or invalid record identifier.")}
        kind = record.get('kind')
        data = record.get('data') if isinstance(record.get('data'), dict) else {}
        handlers = {
            'service_job': ('cl.rice.service.job', self._cl_create_service_job),
            'farmer': ('res.partner', self._cl_create_farmer),
            'trip': ('cl.rice.trip', self._cl_create_trip),
        }
        if kind not in handlers:
            return {'uuid': uuid, 'status': 'failed', 'error': self.env._("Unknown record type.")}
        model, handler = handlers[kind]
        # sudo: detect a record already received even if the officer can no longer read it
        existing = self.env[model].sudo().with_context(active_test=False).search(
            [('cl_client_uuid', '=', uuid)], limit=1)
        if existing:
            return {'uuid': uuid, 'status': 'duplicate', 'id': existing.id}
        try:
            with self.env.cr.savepoint():
                created = handler(uuid, data)
        except IntegrityError:
            # the same record arrived twice at the same time: the unique index kept one
            return {'uuid': uuid, 'status': 'duplicate'}
        except (AccessError, UserError, ValidationError, ValueError, TypeError) as error:
            return {'uuid': uuid, 'status': 'failed', 'error': str(error)}
        return {'uuid': uuid, 'status': 'synced', 'id': created.id}

    @api.model
    def _cl_create_service_job(self, uuid, data):
        fs = self.env['cl.rice.farmer.season'].browse(int(data.get('farmer_season_id') or 0)).exists()
        if not fs:
            raise UserError(self.env._("Farmer not found."))
        fs.check_access('read')
        if fs.field_officer_id != self.env.user:
            raise AccessError(self.env._("This farmer is not assigned to you."))
        service_type = data.get('service_type')
        if service_type not in JOB_SERVICE_TYPES:
            raise UserError(self.env._("Unknown service."))
        values = {
            'farmer_season_id': fs.id,
            'service_type': service_type,
            'date': fields.Date.to_date(data.get('date')) or fields.Date.context_today(self),
            'quantity': float(data.get('quantity') or 0.0),
            'fuel_litres': float(data.get('fuel_litres') or 0.0),
            'note': (data.get('note') or '')[:2000],
            'cl_client_uuid': uuid,
            'source': 'field',
        }
        if data.get('vehicle_id'):
            values['vehicle_id'] = int(data['vehicle_id'])
        if data.get('operator_id'):
            employee = self.env['hr.employee.public'].browse(int(data['operator_id'])).exists()
            values['operator_id'] = employee.id
        if data.get('photo') and isinstance(data['photo'], str):
            values['photo'] = data['photo'].split(',')[-1]  # accept data URLs
        # the job stays a draft: the office checks it before the farmer is charged
        return self.env['cl.rice.service.job'].create(values)

    @api.model
    def _cl_create_trip(self, uuid, data):
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(self.env._("Your user is not linked to an employee: ask the office."))
        vehicle = self.env['fleet.vehicle'].browse(int(data.get('vehicle_id') or 0)).exists()
        if not vehicle:
            raise UserError(self.env._("Vehicle not found."))
        fs = self.env['cl.rice.farmer.season'].browse(int(data.get('farmer_season_id') or 0)).exists()
        if fs and fs.field_officer_id != self.env.user:
            raise AccessError(self.env._("This farmer is not assigned to you."))
        try:
            values = {
                'odometer_start': float(data.get('odometer_start') or 0.0),
                'odometer_end': float(data.get('odometer_end') or 0.0),
                'fuel_litres': float(data.get('fuel_litres') or 0.0),
            }
        except (TypeError, ValueError):
            raise UserError(self.env._("Invalid number in the trip.")) from None
        # the trip stays a draft: the office checks it before posting its cost
        return self.env['cl.rice.trip'].create({
            **values,
            'employee_id': employee.id,
            'vehicle_id': vehicle.id,
            'date': fields.Date.to_date(data.get('date')) or fields.Date.context_today(self),
            'purpose': (data.get('purpose') or self.env._("Field visit"))[:128],
            'farmer_season_id': fs.id,
            'village': (data.get('village') or '')[:64],
            'cl_client_uuid': uuid,
            'source': 'field',
        })

    @api.model
    def _cl_create_farmer(self, uuid, data):
        name = (data.get('name') or '').strip()
        if not name:
            raise UserError(self.env._("The farmer's name is required."))
        duplicates = self.env['res.partner']._cl_find_duplicates(phone=data.get('phone'))['phone']
        note = ''
        if duplicates:
            # kept as pending: the office decides whether it is the same person
            note = self.env._("Possible duplicate of %s (same phone).", ", ".join(duplicates.mapped('name')))
        try:
            latitude = float(data.get('latitude') or 0.0)
            longitude = float(data.get('longitude') or 0.0)
            acreage = float(data.get('acreage') or 0.0)
        except (TypeError, ValueError):
            raise UserError(self.env._("Invalid number in the registration.")) from None
        return self.env['res.partner'].create({
            'name': name[:128],
            'is_farmer': True,
            'cl_farmer_state': 'pending',
            'phone': (data.get('phone') or '')[:32],
            'cl_village': (data.get('village') or '')[:64],
            'cl_ward': (data.get('ward') or '')[:64],
            'cl_total_acreage': acreage,
            'cl_farm_latitude': latitude,
            'cl_farm_longitude': longitude,
            'cl_field_officer_id': self.env.uid,
            'cl_client_uuid': uuid,
            'comment': note,
        })
