import logging
from collections import defaultdict

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Season projects get their analytic account and everything of the season points at it (G9).

    Records tagged by hand before this version (marked MCP-BACKFILL on the test database) are
    reused, never duplicated.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    _link_season_project_accounts(env)
    _backfill_journal_items(env)
    _backfill_expenses(env)
    _backfill_pickings(env)
    companies = env['res.company'].search([])
    companies._cl_rice_setup_company()
    companies._cl_rice_setup_documents()
    env['res.company']._cl_rice_enable_analytic()


def _link_season_project_accounts(env):
    Account = env['account.analytic.account'].with_context(active_test=False)
    for season in env['cl.rice.season'].with_context(active_test=False).search([('project_id', '!=', False)]):
        project = season.project_id
        if not project.account_id:
            # an account prepared by hand for this season (e.g. "MCP-BF-2627") becomes the project account
            manual = Account.search([('code', '=', f"MCP-BF-{season.code}")], limit=1)
            if manual:
                project.account_id = manual
        season._cl_ensure_project_account()
        _logger.info("cl_rice_mill: season %s uses project account %s", season.name, project.account_id.code)


def _season_for_line(line, seasons_by_plan_account, seasons):
    for key in line.analytic_distribution or {}:
        for account_id in key.split(','):
            season = seasons_by_plan_account.get(int(account_id))
            if season:
                return season
    dated = seasons.filtered(lambda s: s.date_start <= line.date <= s.date_end)
    return dated[:1]


def _backfill_journal_items(env):
    """Add the season project account to the rice mill journal items that miss it."""
    seasons = env['cl.rice.season'].with_context(active_test=False).search([('project_id.account_id', '!=', False)])
    if not seasons:
        return
    rice_plans = (
        env.ref('cl_rice_mill.analytic_plan_farmer_seasons')
        | env.ref('cl_rice_mill.analytic_plan_machines')
        | env.ref('cl_rice_mill.analytic_plan_service_lines')
    )
    rice_accounts = env['account.analytic.account'].with_context(active_test=False).search(
        [('root_plan_id', 'in', rice_plans.ids)])
    seasons_by_plan_account = {}
    for season in seasons:
        fs_accounts = env['cl.rice.farmer.season'].with_context(active_test=False).search(
            [('season_id', '=', season.id)]).analytic_account_id
        for account in fs_accounts:
            seasons_by_plan_account[account.id] = season
    project_accounts = set(seasons.project_id.account_id.ids)
    lines = env['account.move.line'].search([('analytic_distribution', 'in', rice_accounts.ids)])
    updates = defaultdict(lambda: env['account.move.line'])
    for line in lines:
        present = {int(i) for key in line.analytic_distribution for i in key.split(',') if i}
        if present & project_accounts:
            continue  # already tagged (by hand or by the module)
        season = _season_for_line(line, seasons_by_plan_account, seasons)
        if not season:
            continue
        project_account = season.project_id.account_id.id
        distribution = {f"{key},{project_account}": value for key, value in line.analytic_distribution.items()}
        updates[frozenset(distribution.items())] |= line
    for distribution, group in updates.items():
        group.write({'analytic_distribution': dict(distribution)})
    _logger.info("cl_rice_mill: %s journal items tagged with their season project", sum(len(g) for g in updates.values()))


def _backfill_expenses(env):
    expenses = env['hr.expense'].search([('company_id.cl_rice_warehouse_id', '!=', False)])
    for expense in expenses:
        distribution = expense._cl_rice_distribution(expense.analytic_distribution)
        if distribution != (expense.analytic_distribution or {}):
            expense.analytic_distribution = distribution


def _backfill_pickings(env):
    Picking = env['stock.picking']
    pickings = Picking.search([('project_id', '=', False), ('move_line_ids.lot_id.product_id.cl_rice_product', '!=', False)])
    for picking in pickings:
        lots = picking.move_line_ids.lot_id
        lines = [{'lot': lot} for lot in lots]
        project = Picking._cl_rice_project(lines, picking.company_id)
        if project:
            picking.project_id = project
