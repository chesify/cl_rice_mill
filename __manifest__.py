{
    'name': "Rice Mill (Luvanda)",
    'version': '20.0.1.0.0',
    'category': 'Agriculture',
    'summary': "Farmer financing, services, paddy storage, milling, sales on behalf and settlement",
    'description': """
Rice Mill management for mills that finance, serve, store and mill rice for
smallholder farmers, sell the crop on their behalf and settle with them.

* Farmers, seasons (msimu) and the farmer-season account
* Loans with approval, interest and credit limits
* Tractor / harvester service jobs, operators paid on behalf
* Paddy intake into farmer-owned stock, milling, storage fees
* Sales on behalf of farmers (agent or owner mode) and settlement statements
* Machine profitability, field trips, small expenses
* Farmer self-service portal (Swahili by default)
""",
    'author': "Chesify Labs",
    'website': "https://chesifylabs.co.tz",
    'license': 'LGPL-3',
    'depends': [
        'account',
        'account_fleet',
        'analytic',
        'fleet',
        'hr',
        'hr_expense',
        'mail',
        'portal',
        'project',
        'sales_team',
        'sale_stock',
        'stock',
        'stock_account',
        'uom',
    ],
    'data': [
        'security/cl_rice_mill_security.xml',
        'security/ir.access.csv',
        'data/res_lang_data.xml',
        'data/ir_sequence_data.xml',
        'data/analytic_data.xml',
        'data/product_data.xml',
        'data/ir_cron_data.xml',
        'wizard/cl_rice_reason_views.xml',
        'wizard/cl_rice_settlement_create_views.xml',
        'views/res_config_settings_views.xml',
        'views/cl_rice_farmer_group_views.xml',
        'views/cl_rice_season_views.xml',
        'views/cl_rice_farmer_season_views.xml',
        'views/res_partner_views.xml',
        'views/cl_rice_loan_views.xml',
        'views/cl_rice_service_rate_views.xml',
        'views/cl_rice_service_job_views.xml',
        'views/fleet_vehicle_views.xml',
        'views/cl_rice_intake_views.xml',
        'views/cl_rice_milling_job_views.xml',
        'views/cl_rice_storage_fee_views.xml',
        'views/stock_lot_views.xml',
        'views/cl_rice_ownership_transfer_views.xml',
        'views/cl_rice_proceeds_views.xml',
        'views/cl_rice_settlement_views.xml',
        'views/sale_order_views.xml',
        'views/cl_rice_menus.xml',
    ],
    'post_init_hook': '_cl_rice_post_init',
    'application': True,
    'installable': True,
}
