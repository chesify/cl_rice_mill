from odoo import http
from odoo.http import request


class OpeningBalanceController(http.Controller):

    @http.route('/rice/opening_balance/template', type='http', auth='user', methods=['GET'])
    def opening_balance_template(self):
        if not request.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            return request.not_found()
        content = request.env['cl.rice.opening.balance']._cl_template_xlsx()
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="opening_balances_template.xlsx"'),
        ])
