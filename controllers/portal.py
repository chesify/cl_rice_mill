import re

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request
from odoo.http.stream import content_disposition

from odoo.addons.portal.controllers.portal import CustomerPortal

# document kind -> (model, report)
PORTAL_DOCUMENTS = {
    'receipt': ('cl.rice.intake', 'cl_rice_mill.action_report_warehouse_receipt'),
    'loan': ('cl.rice.loan', 'cl_rice_mill.action_report_loan_agreement'),
    'milling': ('cl.rice.milling.job', 'cl_rice_mill.action_report_milling_certificate'),
    'settlement': ('cl.rice.settlement', 'cl_rice_mill.action_report_settlement_statement'),
}


class RiceMillPortal(CustomerPortal):
    """The farmer's own account: balance, stock, timeline, statement, documents and requests.

    Records are searched with the farmer's rights (portal access rows limit them to the farmer's
    own data) and only displayed with sudo afterwards.
    """

    def _prepare_portal_counter_values(self, counter):
        if counter == 'rice_season_count':
            return 'cl.rice.farmer.season', self._cl_rice_domain(), 'read'
        return super()._prepare_portal_counter_values(counter)

    def _cl_rice_domain(self):
        return [('partner_id', 'child_of', [request.env.user.partner_id.commercial_partner_id.id])]

    def _cl_rice_farmer_season(self, fs_id):
        """The farmer season if it belongs to the logged-in farmer (raises otherwise), sudoed for display."""
        fs = request.env['cl.rice.farmer.season'].browse(fs_id)
        if not fs.exists():
            raise MissingError(request.env._("This season does not exist."))
        fs.check_access('read')
        return fs.sudo()

    def _cl_rice_parse_date(self, value):
        try:
            return fields.Date.to_date(value) if value else None
        except ValueError:
            return None

    @http.route(['/my/rice'], type='http', auth='user', website=True, readonly=True)
    def portal_my_rice(self, **kw):
        FarmerSeason = request.env['cl.rice.farmer.season']
        if not FarmerSeason.has_access('read'):
            return request.redirect('/my')
        seasons = FarmerSeason.search(self._cl_rice_domain(), order='id desc').sudo()
        held = seasons._cl_stock_held_by_season()
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'rice',
            'farmer_seasons': seasons,
            'stock_held': held,
            'last_events': {fs.id: (fs._cl_portal_timeline()[:1] or [None])[0] for fs in seasons},
        })
        return request.render('cl_rice_mill.portal_my_rice', values)

    @http.route(['/my/rice/<int:fs_id>'], type='http', auth='user', website=True, readonly=True)
    def portal_my_rice_season(self, fs_id, **kw):
        try:
            fs = self._cl_rice_farmer_season(fs_id)
        except (AccessError, MissingError):
            return request.redirect('/my/rice')
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'rice_season',
            'fs': fs,
            'stock': fs._cl_portal_stock(),
            'timeline': fs._cl_portal_timeline(),
            'request_sent': kw.get('request_sent'),
        })
        return request.render('cl_rice_mill.portal_my_rice_season', values)

    @http.route(['/my/rice/<int:fs_id>/statement'], type='http', auth='user', website=True, readonly=True)
    def portal_my_rice_statement(self, fs_id, date_from=None, date_to=None, download=None, **kw):
        try:
            fs = self._cl_rice_farmer_season(fs_id)
        except (AccessError, MissingError):
            return request.redirect('/my/rice')
        date_from, date_to = self._cl_rice_parse_date(date_from), self._cl_rice_parse_date(date_to)
        if download:
            return self._cl_rice_pdf(fs, 'cl_rice_mill.action_report_farmer_statement', f"Statement-{fs.name}", data={
                'farmer_season_ids': fs.ids,
                'date_from': date_from and fields.Date.to_string(date_from),
                'date_to': date_to and fields.Date.to_string(date_to),
            })
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'rice_statement',
            'fs': fs,
            'statement': fs._cl_statement(date_from, date_to),
            'date_from': date_from and fields.Date.to_string(date_from) or '',
            'date_to': date_to and fields.Date.to_string(date_to) or '',
        })
        return request.render('cl_rice_mill.portal_my_rice_statement', values)

    @http.route(['/my/rice/document/<string:kind>/<int:res_id>'], type='http', auth='user', website=True,
                readonly=True)
    def portal_my_rice_document(self, kind, res_id, **kw):
        if kind not in PORTAL_DOCUMENTS:
            return request.not_found()
        model, report = PORTAL_DOCUMENTS[kind]
        record = request.env[model].browse(res_id)
        try:
            if not record.exists():
                raise MissingError(request.env._("This document does not exist."))
            record.check_access('read')
        except (AccessError, MissingError):
            return request.redirect('/my/rice')
        return self._cl_rice_pdf(record.sudo(), report, f"{kind.title()}-{record.sudo().display_name}")

    def _cl_rice_pdf(self, records, report, name, data=None):
        Report = request.env['ir.actions.report'].sudo().with_company(records.company_id[:1])
        pdf = Report._render_qweb_pdf(report, records.ids, data=data)[0]
        filename = re.sub(r'[^\w.-]+', '-', name) + '.pdf'
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
            ('Content-Disposition', content_disposition(filename))])

    @http.route(['/my/rice/<int:fs_id>/request'], type='http', auth='user', website=True, methods=['POST'])
    def portal_my_rice_request(self, fs_id, request_type=None, message=None, **kw):
        try:
            fs = self._cl_rice_farmer_season(fs_id)
        except (AccessError, MissingError):
            return request.redirect('/my/rice')
        if request_type not in ('service', 'loan') or not (message or '').strip():
            raise UserError(request.env._("Choose what you need and describe it."))
        fs._cl_portal_request(request_type, message.strip()[:2000])
        return request.redirect(f'/my/rice/{fs.id}?request_sent=1')
