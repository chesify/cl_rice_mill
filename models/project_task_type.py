from odoo import fields, models

# season stage key -> stage name, in the order a farmer goes through the season
TASK_STAGES = [
    ('registered', "Registered"),
    ('land_prep', "Land Prep"),
    ('planting', "Planting"),
    ('harvest', "Harvest"),
    ('in_storage', "In Storage"),
    ('milling', "Milling"),
    ('released', "Released"),
    ('settled', "Settled"),
]

# farmer season state -> stage its task moves to
STATE_STAGES = {
    'draft': 'registered',
    'active': 'registered',
    'harvested': 'harvest',
    'in_storage': 'in_storage',
    'released': 'released',
    'settled': 'settled',
    'closed': 'settled',
}


class ProjectTaskType(models.Model):
    _inherit = 'project.task.type'

    cl_rice_stage = fields.Selection(
        TASK_STAGES, string="Rice Mill Stage", copy=False,
        help="Farmer tasks of the season project move to this stage automatically.")
