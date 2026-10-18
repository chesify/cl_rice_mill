from . import models
from . import wizard
from . import report
from . import controllers


def _cl_rice_post_init(env):
    companies = env['res.company'].search([])
    companies._cl_rice_setup_company()
    companies._cl_rice_setup_documents()
    env['res.company']._cl_rice_enable_analytic()
