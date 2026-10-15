from odoo import api, models


class ReportGatePass(models.AbstractModel):
    _name = 'report.cl_rice_mill.report_gate_pass'
    _description = "Gate Pass"

    @api.model
    def _get_report_values(self, docids, data=None):
        releases = self.env['cl.rice.stock.release'].browse(docids)
        releases._cl_check_gate_pass()
        return {
            'doc_ids': releases.ids,
            'doc_model': 'cl.rice.stock.release',
            'docs': releases,
        }
