{
    'name': "Rice Mill (Luvanda)",
    'version': '20.0.3.0.0',
    'category': 'Agriculture',
    'summary': "Farmer loans and services, paddy storage, milling, stock release control and settlement",
    'description': """
Rice Mill management for mills that finance, serve, store and mill rice for
smallholder farmers, control the release of the farmers' stock to their buyers
and settle with them.

* Farmers, seasons and the farmer-season account
* Interest-free loans with approval and credit limits
* Tractor / harvester service jobs, operators paid on behalf
* Paddy intake into farmer-owned stock, milling, storage fees
* Bran and husk kept by the mill and sold as its own stock
* Facilitated sales: stock release blocked while the farmer owes, gate passes
* Payments received, funds held for farmers, settlement and payout
* Setup wizard, opening balance import and a simple cash book
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
        'project_stock',
        'sales_team',
        'sale_stock',
        'stock',
        'stock_account',
        'uom',
    ],
    'external_dependencies': {'python': ['openpyxl', 'xlsxwriter']},
    'data': [
        'security/cl_rice_mill_security.xml',
        'security/ir.access.csv',
        'data/ir_sequence_data.xml',
        'data/analytic_data.xml',
        'data/product_data.xml',
        'data/product_expense_data.xml',
        'data/ir_cron_data.xml',
        'report/cl_rice_stock_release_reports.xml',
        'report/cl_rice_stock_release_templates.xml',
        'wizard/cl_rice_reason_views.xml',
        'wizard/cl_rice_settlement_create_views.xml',
        'wizard/cl_rice_setup_views.xml',
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
        'views/cl_rice_farmer_payment_views.xml',
        'views/cl_rice_facilitated_sale_views.xml',
        'views/cl_rice_stock_release_views.xml',
        'views/cl_rice_settlement_views.xml',
        'views/cl_rice_opening_balance_views.xml',
        'views/cl_rice_report_cash_book_views.xml',
        'views/cl_rice_trip_views.xml',
        'views/hr_expense_views.xml',
        'views/cl_rice_field_app_templates.xml',
        'views/cl_rice_menus.xml',
    ],
    'post_init_hook': '_cl_rice_post_init',
    'application': True,
    'installable': True,
}
