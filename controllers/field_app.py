import json

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.tools import file_open

APP_SCOPE = '/rice/field/'


class FieldAppController(http.Controller):
    """Offline field app (PWA) for field officers: a light page, its service worker and two JSON endpoints."""

    def _check_officer(self):
        if not request.env.user.has_group('cl_rice_mill.cl_rice_mill_group_user'):
            raise AccessError(request.env._("The field app is for rice mill staff only."))

    @http.route(['/rice/field', APP_SCOPE], type='http', auth='user', methods=['GET'])
    def field_app(self):
        if request.httprequest.path != APP_SCOPE:
            return request.redirect(APP_SCOPE)
        if not request.env.user.has_group('cl_rice_mill.cl_rice_mill_group_user'):
            return request.not_found()
        return request.render('cl_rice_mill.field_app_page', {'user': request.env.user})

    @http.route(APP_SCOPE + 'manifest.webmanifest', type='http', auth='public', methods=['GET'])
    def field_app_manifest(self):
        manifest = {
            'name': "Rice Mill Field App",
            'short_name': "Rice Field",
            'start_url': APP_SCOPE,
            'scope': APP_SCOPE,
            'display': 'standalone',
            'background_color': '#ffffff',
            'theme_color': '#2e7d32',
            'icons': [{'src': '/cl_rice_mill/static/description/icon.png', 'sizes': '512x512', 'type': 'image/png'}],
        }
        return request.make_response(
            json.dumps(manifest), headers=[('Content-Type', 'application/manifest+json')])

    @http.route(APP_SCOPE + 'sw.js', type='http', auth='public', methods=['GET'])
    def field_app_service_worker(self):
        # served from the app scope so that it controls /rice/field/ (not from /cl_rice_mill/static)
        with file_open('cl_rice_mill/static/src/field_app/service_worker.js', 'rb') as script:
            content = script.read()
        return request.make_response(content, headers=[
            ('Content-Type', 'text/javascript'),
            ('Service-Worker-Allowed', APP_SCOPE),
            ('Cache-Control', 'no-cache'),
        ])

    @http.route(APP_SCOPE + 'data', type='jsonrpc', auth='user', methods=['POST'])
    def field_app_data(self):
        self._check_officer()
        return request.env['cl.rice.field.sync']._cl_bootstrap()

    @http.route(APP_SCOPE + 'sync', type='jsonrpc', auth='user', methods=['POST'])
    def field_app_sync(self, records=None):
        self._check_officer()
        return request.env['cl.rice.field.sync']._cl_sync(records or [])
