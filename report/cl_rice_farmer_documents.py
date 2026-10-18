from odoo import api, fields, models


class ReportFarmerStatement(models.AbstractModel):
    _name = 'report.cl_rice_mill.report_farmer_statement'
    _description = "Farmer Account Statement"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        farmer_seasons = self.env['cl.rice.farmer.season'].browse(data.get('farmer_season_ids') or docids)
        farmer_seasons.check_access('read')
        date_from = fields.Date.to_date(data.get('date_from')) or None
        date_to = fields.Date.to_date(data.get('date_to')) or None
        docs = farmer_seasons.sudo()
        return {
            'doc_ids': docs.ids,
            'doc_model': 'cl.rice.farmer.season',
            'docs': docs,
            'statements': {fs.id: fs._cl_statement(date_from, date_to) for fs in docs},
        }


class ReportLoanAgreement(models.AbstractModel):
    _name = 'report.cl_rice_mill.report_loan_agreement'
    _description = "Loan Agreement"

    @api.model
    def _get_report_values(self, docids, data=None):
        loans = self.env['cl.rice.loan'].browse(docids)
        loans.check_access('read')
        # the farmer's ID number is printed on the agreement the farmer signs
        docs = loans.sudo()
        return {'doc_ids': docs.ids, 'doc_model': 'cl.rice.loan', 'docs': docs}
