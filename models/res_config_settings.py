from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cl_rice_ltv_percent = fields.Float(related='company_id.cl_rice_ltv_percent', readonly=False)
    cl_rice_default_yield = fields.Float(related='company_id.cl_rice_default_yield', readonly=False)
    cl_rice_min_cover_percent = fields.Float(related='company_id.cl_rice_min_cover_percent', readonly=False)

    cl_rice_broken_policy = fields.Selection(related='company_id.cl_rice_broken_policy', readonly=False)
    cl_rice_byproduct_fee_policy = fields.Selection(related='company_id.cl_rice_byproduct_fee_policy', readonly=False)

    cl_rice_storage_enabled = fields.Boolean(related='company_id.cl_rice_storage_enabled', readonly=False)
    cl_rice_storage_basis = fields.Selection(related='company_id.cl_rice_storage_basis', readonly=False)
    cl_rice_storage_rate = fields.Monetary(related='company_id.cl_rice_storage_rate', readonly=False)
    cl_rice_storage_free_days = fields.Integer(related='company_id.cl_rice_storage_free_days', readonly=False)
    cl_rice_paddy_bag_kg = fields.Float(related='company_id.cl_rice_paddy_bag_kg', readonly=False)
    cl_rice_rice_bag_kg = fields.Float(related='company_id.cl_rice_rice_bag_kg', readonly=False)
    cl_rice_bag_tare = fields.Float(related='company_id.cl_rice_bag_tare', readonly=False)

    cl_rice_warehouse_id = fields.Many2one(related='company_id.cl_rice_warehouse_id', readonly=False)
    cl_rice_paddy_location_id = fields.Many2one(related='company_id.cl_rice_paddy_location_id', readonly=False)
    cl_rice_rice_location_id = fields.Many2one(related='company_id.cl_rice_rice_location_id', readonly=False)
    cl_rice_byproduct_location_id = fields.Many2one(related='company_id.cl_rice_byproduct_location_id', readonly=False)
    cl_rice_released_location_id = fields.Many2one(related='company_id.cl_rice_released_location_id', readonly=False)

    cl_rice_loan_account_id = fields.Many2one(related='company_id.cl_rice_loan_account_id', readonly=False)
    cl_rice_service_account_id = fields.Many2one(related='company_id.cl_rice_service_account_id', readonly=False)
    cl_rice_funds_held_account_id = fields.Many2one(related='company_id.cl_rice_funds_held_account_id', readonly=False)
    cl_rice_tractor_income_account_id = fields.Many2one(
        related='company_id.cl_rice_tractor_income_account_id', readonly=False)
    cl_rice_harvester_income_account_id = fields.Many2one(
        related='company_id.cl_rice_harvester_income_account_id', readonly=False)
    cl_rice_storage_income_account_id = fields.Many2one(
        related='company_id.cl_rice_storage_income_account_id', readonly=False)
    cl_rice_milling_income_account_id = fields.Many2one(
        related='company_id.cl_rice_milling_income_account_id', readonly=False)
    cl_rice_mill_hours_per_day = fields.Float(related='company_id.cl_rice_mill_hours_per_day', readonly=False)
    cl_rice_dashboard_lot_age_days = fields.Integer(related='company_id.cl_rice_dashboard_lot_age_days', readonly=False)
    cl_rice_byproduct_income_account_id = fields.Many2one(
        related='company_id.cl_rice_byproduct_income_account_id', readonly=False)
    cl_rice_other_income_account_id = fields.Many2one(
        related='company_id.cl_rice_other_income_account_id', readonly=False)

    cl_rice_charge_journal_id = fields.Many2one(related='company_id.cl_rice_charge_journal_id', readonly=False)
    cl_rice_walkin_journal_id = fields.Many2one(related='company_id.cl_rice_walkin_journal_id', readonly=False)
    cl_rice_loan_journal_id = fields.Many2one(related='company_id.cl_rice_loan_journal_id', readonly=False)
    cl_rice_settlement_journal_id = fields.Many2one(
        related='company_id.cl_rice_settlement_journal_id', readonly=False)

    cl_rice_fuel_price = fields.Monetary(related='company_id.cl_rice_fuel_price', readonly=False)
    cl_rice_post_expenses = fields.Boolean(related='company_id.cl_rice_post_expenses', readonly=False)
    cl_rice_fuel_clearing_account_id = fields.Many2one(
        related='company_id.cl_rice_fuel_clearing_account_id', readonly=False)
    cl_rice_machine_cost_account_id = fields.Many2one(
        related='company_id.cl_rice_machine_cost_account_id', readonly=False)
    cl_rice_operator_wage_account_id = fields.Many2one(
        related='company_id.cl_rice_operator_wage_account_id', readonly=False)
    cl_rice_operator_accrual_account_id = fields.Many2one(
        related='company_id.cl_rice_operator_accrual_account_id', readonly=False)
    cl_rice_cost_journal_id = fields.Many2one(related='company_id.cl_rice_cost_journal_id', readonly=False)

    def action_cl_rice_open_setup(self):
        self.ensure_one()
        return self.env['ir.actions.act_window']._for_xml_id('cl_rice_mill.cl_rice_setup_action')
