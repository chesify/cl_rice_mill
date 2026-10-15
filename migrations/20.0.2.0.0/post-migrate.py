import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# accounts of the removed interest, commission and sales-on-behalf flows
OBSOLETE_ACCOUNT_CODES = ['RM4310', 'RM2310', 'RM2320', 'RM4360']
# noupdate records of version 1 that no longer exist in the module data
OBSOLETE_XMLIDS = [
    'analytic_service_commission',
    'analytic_service_interest',
    'analytic_service_trading',
    'ir_cron_cl_rice_accrue_interest',
    'ir_cron_cl_rice_accrue_interest_ir_actions_server',
    'seq_cl_rice_ownership_transfer',
]
# tables of the removed models: Odoo leaves them in the database
OBSOLETE_TABLES = ['cl_rice_loan_interest', 'cl_rice_proceeds', 'cl_rice_ownership_transfer']
# warehouses of version 1, replaced by one warehouse with partitions
OBSOLETE_WAREHOUSE_CODES = ['GMP', 'GMC']
# product names of version 1, renamed in English
PRODUCT_NAMES = {
    'product_paddy': ("Paddy (Mpunga)", "Paddy", 'PADDY'),
    'product_rice': ("Milled Rice (Mchele)", "Milled Rice", 'RICE'),
    'product_broken': ("Broken Rice (Mchele Vipande)", "Broken Rice", 'BROKEN'),
    'product_bran': ("Bran (Pumba)", "Bran", 'BRAN'),
    'product_husk': ("Husk (Makapi)", "Husk", 'HUSK'),
}


def migrate(cr, version):
    """Clean what version 1 leaves behind once interest, commission and the sales on behalf of
    farmers are removed. Odoo drops the columns of removed fields, but not the tables of removed
    models, nor noupdate data and the accounts created by the setup: those are handled here.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})

    for table in OBSOLETE_TABLES:
        cr.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')  # noqa: S608 (constant table names)

    # farmer seasons: the 'sold' state is now 'released'
    cr.execute("UPDATE cl_rice_farmer_season SET state = 'released' WHERE state = 'sold'")

    # noupdate records: delete when nothing uses them, otherwise archive them
    for xmlid in OBSOLETE_XMLIDS:
        record = env.ref(f'cl_rice_mill.{xmlid}', raise_if_not_found=False)
        if not record:
            continue
        if record._name == 'account.analytic.account' and record.line_ids:
            record.active = False
            _logger.info("cl_rice_mill: analytic account %s has entries, archived", record.name)
            continue
        record.unlink()

    # accounts of the removed flows: delete if never used, else archive and rename
    Account = env['account.account'].with_context(active_test=False)
    for company in env['res.company'].search([]):
        accounts = Account.with_company(company).search([
            *Account._check_company_domain(company),
            ('code', 'in', OBSOLETE_ACCOUNT_CODES),
        ])
        for account in accounts:
            used = env['account.move.line'].search_count([('account_id', '=', account.id)], limit=1)
            if used:
                account.write({'active': False, 'name': f"{account.name} (obsolete)"})
                _logger.warning(
                    "cl_rice_mill: account %s %s has journal items and was archived, not deleted",
                    account.code, account.name)
            else:
                account.unlink()

    # products: English names, unless renamed by the user
    for xmlid, (old_name, new_name, code) in PRODUCT_NAMES.items():
        product = env.ref(f'cl_rice_mill.{xmlid}', raise_if_not_found=False)
        if product and product.name == old_name:
            product.write({'name': new_name, 'default_code': code})

    # the single warehouse with its partitions, the new accounts and journals
    env['res.company'].search([])._cl_rice_setup_company()

    # the two warehouses of version 1: archived when empty, kept (and reported) otherwise
    for warehouse in env['stock.warehouse'].search([('code', 'in', OBSOLETE_WAREHOUSE_CODES)]):
        stock = env['stock.quant'].search_count([
            ('location_id', 'child_of', warehouse.view_location_id.id), ('quantity', '!=', 0)], limit=1)
        if stock:
            _logger.warning(
                "cl_rice_mill: warehouse %s still holds stock; move it to the Rice Mill Warehouse partitions",
                warehouse.name)
        else:
            warehouse.active = False
