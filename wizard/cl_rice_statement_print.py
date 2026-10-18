from odoo import api, fields, models


class ClRiceStatementPrint(models.TransientModel):
    _name = 'cl.rice.statement.print'
    _description = "Print Farmer Account Statement"

    farmer_season_id = fields.Many2one('cl.rice.farmer.season', string="Farmer Season", required=True)
    partner_id = fields.Many2one(related='farmer_season_id.partner_id')
    date_from = fields.Date(string="From", help="Leave empty to start at the beginning of the season.")
    date_to = fields.Date(string="To", help="Leave empty to include everything up to today.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        context = self.env.context
        if not values.get('farmer_season_id'):
            if context.get('active_model') == 'cl.rice.farmer.season' and context.get('active_id'):
                values['farmer_season_id'] = context['active_id']
            elif context.get('active_model') == 'res.partner' and context.get('active_id'):
                fs = self.env['cl.rice.farmer.season'].search(
                    [('partner_id', '=', context['active_id'])], order='id desc', limit=1)
                values['farmer_season_id'] = fs.id
        return values

    def action_print(self):
        self.ensure_one()
        return self.env.ref('cl_rice_mill.action_report_farmer_statement').report_action(self.farmer_season_id, data={
            'farmer_season_ids': self.farmer_season_id.ids,
            'date_from': self.date_from and fields.Date.to_string(self.date_from),
            'date_to': self.date_to and fields.Date.to_string(self.date_to),
        })
