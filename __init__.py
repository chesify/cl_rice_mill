from . import models
from . import wizard
from . import report
from . import controllers


def _cl_rice_post_init(env):
    env['res.company'].search([])._cl_rice_setup_company()
