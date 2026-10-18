import logging

from odoo import api, SUPERUSER_ID

from odoo.addons.cl_rice_mill.models.project_task_type import STATE_STAGES

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seasons = env['cl.rice.season'].with_context(active_test=False).search([('project_id', '!=', False)])
    # G19: season stages, and each farmer's task put where the farmer season is
    seasons._cl_ensure_task_stages()
    farmer_seasons = env['cl.rice.farmer.season'].with_context(active_test=False).search([('task_id', '!=', False)])
    for fs in farmer_seasons:
        if fs.milling_job_ids.filtered(lambda j: j.state == 'done'):
            fs._cl_move_task('milling')
        fs._cl_move_task(STATE_STAGES.get(fs.state))
    seasons._cl_detach_default_stages()
    # G24: farmer season analytic accounts named after the farmer only (the season is the plan)
    renamed = 0
    for fs in env['cl.rice.farmer.season'].with_context(active_test=False).search([('analytic_account_id', '!=', False)]):
        name = fs._cl_analytic_name()
        if fs.analytic_account_id.name != name:
            fs.analytic_account_id.name = name
            renamed += 1
    _logger.info("cl_rice_mill: season stages set on %s projects, %s analytic accounts renamed", len(seasons), renamed)
