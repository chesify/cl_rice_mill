from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cl_rice_interest_mode = fields.Selection(related='company_id.cl_rice_interest_mode', readonly=False)
    cl_rice_interest_rate = fields.Float(related='company_id.cl_rice_interest_rate', readonly=False)
    cl_rice_interest_accrual = fields.Selection(related='company_id.cl_rice_interest_accrual', readonly=False)
    cl_rice_ltv_percent = fields.Float(related='company_id.cl_rice_ltv_percent', readonly=False)
    cl_rice_default_yield = fields.Float(related='company_id.cl_rice_default_yield', readonly=False)
    cl_rice_min_cover_percent = fields.Float(related='company_id.cl_rice_min_cover_percent', readonly=False)

    cl_rice_sales_mode = fields.Selection(related='company_id.cl_rice_sales_mode', readonly=False)
    cl_rice_commission_type = fields.Selection(related='company_id.cl_rice_commission_type', readonly=False)
    cl_rice_commission_value = fields.Float(related='company_id.cl_rice_commission_value', readonly=False)

    cl_rice_bran_policy = fields.Selection(related='company_id.cl_rice_bran_policy', readonly=False)
    cl_rice_bran_value = fields.Monetary(related='company_id.cl_rice_bran_value', readonly=False)
    cl_rice_husk_policy = fields.Selection(related='company_id.cl_rice_husk_policy', readonly=False)
    cl_rice_husk_value = fields.Monetary(related='company_id.cl_rice_husk_value', readonly=False)
    cl_rice_broken_policy = fields.Selection(related='company_id.cl_rice_broken_policy', readonly=False)
    cl_rice_broken_value = fields.Monetary(related='company_id.cl_rice_broken_value', readonly=False)

    cl_rice_storage_basis = fields.Selection(related='company_id.cl_rice_storage_basis', readonly=False)
    cl_rice_storage_rate = fields.Monetary(related='company_id.cl_rice_storage_rate', readonly=False)
    cl_rice_storage_free_days = fields.Integer(related='company_id.cl_rice_storage_free_days', readonly=False)
    cl_rice_paddy_bag_kg = fields.Float(related='company_id.cl_rice_paddy_bag_kg', readonly=False)
    cl_rice_rice_bag_kg = fields.Float(related='company_id.cl_rice_rice_bag_kg', readonly=False)

    cl_rice_paddy_warehouse_id = fields.Many2one(related='company_id.cl_rice_paddy_warehouse_id', readonly=False)
    cl_rice_rice_warehouse_id = fields.Many2one(related='company_id.cl_rice_rice_warehouse_id', readonly=False)
    cl_rice_mill_location_id = fields.Many2one(related='company_id.cl_rice_mill_location_id', readonly=False)
    cl_rice_consume_type_id = fields.Many2one(related='company_id.cl_rice_consume_type_id', readonly=False)
    cl_rice_output_type_id = fields.Many2one(related='company_id.cl_rice_output_type_id', readonly=False)
    cl_rice_bag_tare = fields.Float(related='company_id.cl_rice_bag_tare', readonly=False)

    cl_rice_split_rice = fields.Float(related='company_id.cl_rice_split_rice', readonly=False)
    cl_rice_split_broken = fields.Float(related='company_id.cl_rice_split_broken', readonly=False)
    cl_rice_split_bran = fields.Float(related='company_id.cl_rice_split_bran', readonly=False)
    cl_rice_split_husk = fields.Float(related='company_id.cl_rice_split_husk', readonly=False)

    cl_rice_loan_account_id = fields.Many2one(related='company_id.cl_rice_loan_account_id', readonly=False)
    cl_rice_service_account_id = fields.Many2one(related='company_id.cl_rice_service_account_id', readonly=False)
    cl_rice_proceeds_account_id = fields.Many2one(related='company_id.cl_rice_proceeds_account_id', readonly=False)
    cl_rice_clearing_account_id = fields.Many2one(related='company_id.cl_rice_clearing_account_id', readonly=False)
    cl_rice_interest_income_account_id = fields.Many2one(
        related='company_id.cl_rice_interest_income_account_id', readonly=False)
    cl_rice_tractor_income_account_id = fields.Many2one(
        related='company_id.cl_rice_tractor_income_account_id', readonly=False)
    cl_rice_harvester_income_account_id = fields.Many2one(
        related='company_id.cl_rice_harvester_income_account_id', readonly=False)
    cl_rice_storage_income_account_id = fields.Many2one(
        related='company_id.cl_rice_storage_income_account_id', readonly=False)
    cl_rice_milling_income_account_id = fields.Many2one(
        related='company_id.cl_rice_milling_income_account_id', readonly=False)
    cl_rice_commission_income_account_id = fields.Many2one(
        related='company_id.cl_rice_commission_income_account_id', readonly=False)
    cl_rice_byproduct_income_account_id = fields.Many2one(
        related='company_id.cl_rice_byproduct_income_account_id', readonly=False)
    cl_rice_other_income_account_id = fields.Many2one(
        related='company_id.cl_rice_other_income_account_id', readonly=False)

    cl_rice_charge_journal_id = fields.Many2one(related='company_id.cl_rice_charge_journal_id', readonly=False)
    cl_rice_walkin_journal_id = fields.Many2one(related='company_id.cl_rice_walkin_journal_id', readonly=False)
    cl_rice_loan_journal_id = fields.Many2one(related='company_id.cl_rice_loan_journal_id', readonly=False)
    cl_rice_settlement_journal_id = fields.Many2one(
        related='company_id.cl_rice_settlement_journal_id', readonly=False)

    def action_cl_rice_setup_company(self):
        self.ensure_one()
        self.company_id._cl_rice_setup_company()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
