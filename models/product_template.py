from odoo import fields, models

RICE_PRODUCTS = [
    ('paddy', "Paddy (Mpunga)"),
    ('rice', "Milled Rice (Mchele)"),
    ('broken', "Broken Rice"),
    ('bran', "Bran (Pumba)"),
    ('husk', "Husk (Makapi)"),
]

# lot name suffix per rice product
LOT_SUFFIX = {'paddy': 'MP', 'rice': 'MC', 'broken': 'MV', 'bran': 'PB', 'husk': 'MK'}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    cl_rice_product = fields.Selection(
        RICE_PRODUCTS, string="Rice Mill Product", index='btree_not_null',
        help="Role of the product in the rice mill: farmer stock of this product is tracked by owner and lot.")
