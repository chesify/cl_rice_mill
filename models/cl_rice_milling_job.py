from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

# output field prefix -> product xml id, policy field on the company (None: always to the paddy owner)
OUTPUTS = [
    ('rice', 'cl_rice_mill.product_rice', None),
    ('broken', 'cl_rice_mill.product_broken', 'cl_rice_broken'),
    ('bran', 'cl_rice_mill.product_bran', 'cl_rice_bran'),
    ('husk', 'cl_rice_mill.product_husk', 'cl_rice_husk'),
]


class ClRiceMillingJob(models.Model):
    _name = 'cl.rice.milling.job'
    _description = "Milling Job (Kukoboa)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    mode = fields.Selection(
        [
            ('farmer', "Farmer (season account)"),
            ('walkin', "Walk-in (pay now)"),
            ('mill', "Mill's own paddy"),
        ],
        required=True, default='farmer', tracking=True)
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", index=True, tracking=True, check_company=True,
        domain="[('state', 'in', ('in_storage', 'harvested', 'sold'))]")
    partner_id = fields.Many2one(
        'res.partner', string="Owner", index=True, tracking=True,
        compute='_compute_partner_id', store=True, readonly=False, precompute=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection(
        [('draft', "Draft"), ('in_progress', "In Progress"), ('done', "Done"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False, index=True)

    paddy_product_id = fields.Many2one(
        'product.product', string="Paddy", required=True, check_company=True,
        domain="[('cl_rice_product', '=', 'paddy')]",
        default=lambda self: self.env.ref('cl_rice_mill.product_paddy', raise_if_not_found=False))
    input_lot_id = fields.Many2one(
        'stock.lot', string="Paddy Lot", check_company=True, tracking=True,
        domain="[('product_id', '=', paddy_product_id), ('cl_owner_id', '=', partner_id)] if partner_id "
               "else [('product_id', '=', paddy_product_id)]")
    available_kg = fields.Float(string="Available in Store (kg)", compute='_compute_available_kg', digits='Product Unit')
    input_kg = fields.Float(string="Paddy In (kg)", digits='Product Unit', tracking=True)
    input_bags = fields.Float(string="Paddy Bags", digits=(16, 1))
    rice_kg = fields.Float(string="Rice (kg)", digits='Product Unit', tracking=True)
    rice_bags = fields.Float(string="Rice Bags", digits=(16, 1))
    broken_kg = fields.Float(string="Broken Rice (kg)", digits='Product Unit')
    broken_bags = fields.Float(string="Broken Bags", digits=(16, 1))
    bran_kg = fields.Float(string="Bran (kg)", digits='Product Unit')
    bran_bags = fields.Float(string="Bran Bags", digits=(16, 1))
    husk_kg = fields.Float(string="Husk (kg)", digits='Product Unit')
    husk_bags = fields.Float(string="Husk Bags", digits=(16, 1))
    loss_kg = fields.Float(string="Loss (kg)", digits='Product Unit', compute='_compute_yield', store=True)
    recovery_pct = fields.Float(
        string="Recovery (%)", digits=(5, 2), compute='_compute_yield', store=True, aggregator='avg',
        help="Milled rice out of paddy in, in percent.")
    total_recovery_pct = fields.Float(
        string="Rice + Broken (%)", digits=(5, 2), compute='_compute_yield', store=True, aggregator='avg')

    vehicle_id = fields.Many2one(
        'fleet.vehicle', string="Mill Machine", check_company=True, domain="[('cl_machine_category', '=', 'mill')]")
    operator_id = fields.Many2one('hr.employee', string="Operator", check_company=True)
    date_start = fields.Datetime(string="Start", default=fields.Datetime.now, tracking=True)
    date_end = fields.Datetime(string="End", tracking=True)

    # fee
    rate_id = fields.Many2one(
        'cl.rice.service.rate', string="Milling Rate", check_company=True,
        compute='_compute_rate_id', store=True, readonly=False, domain="[('service_type', '=', 'milling')]")
    price_unit = fields.Monetary(string="Rate", compute='_compute_fee', store=True, readonly=False)
    fee_basis_qty = fields.Float(string="Charged Quantity", compute='_compute_fee', store=True, digits='Product Unit')
    fee_amount = fields.Monetary(string="Milling Fee", compute='_compute_fee', store=True, tracking=True)
    byproduct_credit = fields.Monetary(
        string="By-product Credit", compute='_compute_byproduct_credit', store=True,
        help="Value of the by-products kept by the mill, credited to the paddy owner.")
    payment_journal_id = fields.Many2one(
        'account.journal', string="Paid Into", check_company=True, domain="[('type', 'in', ('cash', 'bank'))]",
        help="Walk-in customers pay the milling fee on the spot into this journal.")

    consume_picking_id = fields.Many2one('stock.picking', string="Consumption", readonly=True, copy=False)
    output_picking_id = fields.Many2one('stock.picking', string="Output", readonly=True, copy=False)
    invoice_id = fields.Many2one('account.move', string="Invoice", readonly=True, copy=False)
    payment_id = fields.Many2one('account.payment', string="Payment", readonly=True, copy=False)
    credit_move_id = fields.Many2one('account.move', string="By-product Credit Entry", readonly=True, copy=False)
    note = fields.Text()

    _weights_positive = models.Constraint(
        'CHECK(input_kg >= 0 AND rice_kg >= 0 AND broken_kg >= 0 AND bran_kg >= 0 AND husk_kg >= 0 '
        'AND input_bags >= 0 AND rice_bags >= 0 AND broken_bags >= 0 AND bran_bags >= 0 AND husk_bags >= 0)',
        "Weights and bags cannot be negative.")

    @api.depends('farmer_season_id', 'mode')
    def _compute_partner_id(self):
        for job in self:
            if job.mode == 'mill':
                job.partner_id = False
            elif job.farmer_season_id:
                job.partner_id = job.farmer_season_id.partner_id

    @api.depends('input_lot_id', 'partner_id', 'paddy_product_id')
    def _compute_available_kg(self):
        Quant = self.env['stock.quant']
        for job in self:
            if job.input_lot_id:
                job.available_kg = Quant._cl_available_qty(
                    job.paddy_product_id, job.input_lot_id, job.partner_id,
                    job.company_id.cl_rice_paddy_warehouse_id.lot_stock_id)
            else:
                job.available_kg = 0.0

    @api.depends('input_kg', 'rice_kg', 'broken_kg', 'bran_kg', 'husk_kg')
    def _compute_yield(self):
        for job in self:
            outputs = job.rice_kg + job.broken_kg + job.bran_kg + job.husk_kg
            job.loss_kg = job.input_kg - outputs
            job.recovery_pct = job.input_kg and job.rice_kg / job.input_kg * 100.0
            job.total_recovery_pct = job.input_kg and (job.rice_kg + job.broken_kg) / job.input_kg * 100.0

    @api.depends('mode', 'date_start', 'company_id')
    def _compute_rate_id(self):
        Rate = self.env['cl.rice.service.rate']
        for job in self:
            if job.mode == 'mill':
                job.rate_id = False
            elif job.company_id:
                job.rate_id = Rate._cl_get_rate('milling', (job.date_start or fields.Datetime.now()).date(), job.company_id)

    @api.depends('rate_id', 'mode', 'input_kg', 'input_bags', 'price_unit')
    def _compute_fee(self):
        for job in self:
            if job.mode == 'mill':
                job.price_unit = job.fee_basis_qty = job.fee_amount = 0.0
                continue
            rate = job.rate_id
            if rate and not job.price_unit:
                job.price_unit = rate._cl_price('walkin' if job.mode == 'walkin' else 'farmer')
            job.fee_basis_qty = job.input_bags if rate.uom == 'bag' else job.input_kg
            job.fee_amount = job.currency_id.round(job.fee_basis_qty * job.price_unit)

    @api.depends('mode', 'broken_kg', 'bran_kg', 'husk_kg', 'company_id')
    def _compute_byproduct_credit(self):
        for job in self:
            credit = 0.0
            if job.mode != 'mill':
                company = job.company_id
                for prefix, _xml_id, policy in OUTPUTS:
                    if policy and company[f'{policy}_policy'] == 'kept':
                        credit += job[f'{prefix}_kg'] * company[f'{policy}_value']
            job.byproduct_credit = job.currency_id.round(credit)

    @api.constrains('mode', 'farmer_season_id', 'partner_id')
    def _check_owner(self):
        for job in self:
            if job.mode == 'farmer' and not job.farmer_season_id:
                raise ValidationError(self.env._("A farmer milling job needs the farmer season."))
            if job.mode == 'walkin' and not job.partner_id:
                raise ValidationError(self.env._("A walk-in milling job needs the customer."))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for job in self:
            if job.date_start and job.date_end and job.date_end < job.date_start:
                raise ValidationError(self.env._("The milling cannot end before it starts."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.milling.job') or '/'
        jobs = super().create(vals_list)
        jobs.farmer_season_id._cl_check_open()
        return jobs

    def write(self, vals):
        if self.filtered(lambda j: j.state in ('done', 'cancelled')) and set(vals) - {'note', 'date_end'}:
            raise UserError(self.env._("A finished milling job cannot be changed."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda j: j.state == 'done'):
            raise UserError(self.env._("A finished milling job cannot be deleted."))

    @api.onchange('farmer_season_id', 'partner_id')
    def _onchange_owner_set_lot(self):
        if self.farmer_season_id:
            self.input_lot_id = self.farmer_season_id._cl_find_lot(self.paddy_product_id)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_start(self):
        for job in self:
            if job.state != 'draft':
                raise UserError(self.env._("Only draft milling jobs can be started."))
            job.farmer_season_id._cl_check_open()
        self.write({'state': 'in_progress', 'date_start': fields.Datetime.now()})

    def action_done(self):
        for job in self:
            if job.state not in ('draft', 'in_progress'):
                raise UserError(self.env._("Milling job %s is already finished.", job.name))
            job._cl_check_done()
            job._cl_post_stock()
            if job.mode != 'mill':
                job._cl_charge()
            job.write({'state': 'done', 'date_end': job.date_end or fields.Datetime.now()})
            job.message_post(body=self.env._(
                "Milled %(input)s kg of paddy: %(rice)s kg rice (%(pct)s%% recovery).",
                input=job.input_kg, rice=job.rice_kg, pct=round(job.recovery_pct, 2)))
            if job.partner_id:
                job.partner_id._cl_rice_notify('milling_done', job)

    def _cl_check_done(self):
        self.ensure_one()
        self.farmer_season_id._cl_check_open()
        if not self.input_lot_id:
            raise UserError(self.env._("Choose the paddy lot to mill."))
        if self.input_kg <= 0 or self.rice_kg <= 0:
            raise UserError(self.env._("Enter the paddy weight milled and the rice weight obtained."))
        if self.loss_kg < -0.0001:
            raise UserError(self.env._("The outputs weigh more than the paddy milled."))
        available = self.env['stock.quant']._cl_available_qty(
            self.paddy_product_id, self.input_lot_id, self.partner_id,
            self.company_id.cl_rice_paddy_warehouse_id.lot_stock_id)
        if self.input_kg > available + 0.0001:
            raise UserError(self.env._(
                "Only %(available)s kg of paddy is available in lot %(lot)s: you cannot mill %(kg)s kg.",
                available=round(available, 2), lot=self.input_lot_id.name, kg=self.input_kg))
        if self.mode == 'walkin' and not self.payment_journal_id:
            raise UserError(self.env._("Choose the journal the walk-in customer pays into."))

    def _cl_output_lot(self, product, owner):
        self.ensure_one()
        if owner and self.farmer_season_id:
            return self.farmer_season_id._cl_get_lot(product)
        if owner:
            suffix = product.default_code or 'X'
            return self.env['stock.lot'].sudo().create({
                'name': f"{self.name}-{suffix}".replace('/', '-'),
                'product_id': product.id,
                'company_id': self.company_id.id,
                'cl_owner_id': owner.id,
            })
        season = self.season_id or self.input_lot_id.cl_season_id
        return self.env['cl.rice.season']._cl_get_mill_lot(product, season, self.company_id)

    def _cl_post_stock(self):
        self.ensure_one()
        company = self.company_id
        Picking = self.env['stock.picking']
        owner = self.partner_id
        quants = self.env['stock.quant']._cl_owned_quants(
            self.paddy_product_id, self.input_lot_id, owner, company.cl_rice_paddy_warehouse_id.lot_stock_id)
        # cost of mill-owned paddy, split over the outputs by relative value
        input_value = 0.0
        if not owner:
            input_value = sum(quants.mapped('value')) * self.input_kg / (sum(quants.mapped('quantity')) or 1.0)
        consume = Picking._cl_rice_done_transfer(
            company.cl_rice_consume_type_id,
            company.cl_rice_paddy_warehouse_id.lot_stock_id,
            company.cl_rice_mill_location_id,
            owner,
            [{
                'product': self.paddy_product_id, 'qty': self.input_kg, 'lot': self.input_lot_id,
                'bags': self.input_bags, 'quants': quants,
            }],
            origin=self.name,
        )
        weights = {
            'rice': company.cl_rice_split_rice,
            'broken': company.cl_rice_split_broken,
            'bran': company.cl_rice_split_bran,
            'husk': company.cl_rice_split_husk,
        }
        total_weight = sum(self[f'{prefix}_kg'] * weights[prefix] for prefix, _x, _p in OUTPUTS) or 1.0
        lines = []
        for prefix, xml_id, policy in OUTPUTS:
            kg = self[f'{prefix}_kg']
            if kg <= 0:
                continue
            product = self.env.ref(xml_id)
            line_owner = owner
            value = None
            if not owner:
                value = input_value * kg * weights[prefix] / total_weight
            elif policy and company[f'{policy}_policy'] == 'kept':
                line_owner = self.env['res.partner']
                value = kg * company[f'{policy}_value']
            lines.append({
                'product': product, 'qty': kg, 'bags': self[f'{prefix}_bags'],
                'lot': self._cl_output_lot(product, line_owner), 'owner': line_owner, 'value': value,
            })
        output = Picking._cl_rice_done_transfer(
            company.cl_rice_output_type_id,
            company.cl_rice_mill_location_id,
            company.cl_rice_rice_warehouse_id.lot_stock_id,
            owner,
            lines,
            origin=self.name,
        )
        self.write({'consume_picking_id': consume.id, 'output_picking_id': output.id})

    def _cl_analytic_distribution(self):
        self.ensure_one()
        accounts = self.env.ref(
            'cl_rice_mill.analytic_service_milling_walkin' if self.mode == 'walkin'
            else 'cl_rice_mill.analytic_service_milling_farmer')
        if self.vehicle_id:
            accounts |= self.vehicle_id._cl_get_analytic_account()
        if self.farmer_season_id:
            return self.farmer_season_id._cl_analytic_distribution(accounts)
        return {",".join(str(i) for i in accounts.ids): 100.0}

    def _cl_charge(self):
        self.ensure_one()
        company = self.company_id
        fs = self.farmer_season_id
        if self.fee_amount:
            journal = company.cl_rice_walkin_journal_id if self.mode == 'walkin' else company.cl_rice_charge_journal_id
            if not journal or not company.cl_rice_milling_income_account_id:
                raise UserError(self.env._("Set the milling income account and journals in the Rice Mill settings."))
            line_vals = [{
                'name': self.env._("Milling %(kg)s kg paddy - %(job)s", kg=self.input_kg, job=self.name),
                'quantity': self.fee_basis_qty,
                'price_unit': self.price_unit,
                'account_id': company.cl_rice_milling_income_account_id.id,
                'tax_ids': [(6, 0, [])],
                'analytic_distribution': self._cl_analytic_distribution(),
                'vehicle_id': self.vehicle_id.id,
            }]
            if self.mode == 'walkin' and self.byproduct_credit:
                # walk-in customers leave the kept by-products as part payment of the fee
                if self.currency_id.compare_amounts(self.byproduct_credit, self.fee_amount) > 0:
                    raise UserError(self.env._("The by-products kept are worth more than the milling fee."))
                line_vals += self._cl_byproduct_credit_invoice_lines()
            invoice = self.env['account.move'].sudo().create({
                'move_type': 'out_invoice',
                'partner_id': self.partner_id.id,
                'journal_id': journal.id,
                'invoice_date': fields.Date.context_today(self),
                'invoice_origin': self.name,
                'cl_farmer_season_id': fs.id,
                'company_id': company.id,
                'invoice_line_ids': [(0, 0, vals) for vals in line_vals],
            })
            invoice.action_post()
            self.invoice_id = invoice
            if fs:
                fs._cl_tag_receivable_lines(invoice)
            if self.mode == 'walkin':
                self._cl_register_walkin_payment(invoice)
        if self.mode == 'farmer' and self.byproduct_credit:
            self._cl_post_byproduct_credit()

    def _cl_byproduct_lines(self):
        """(product, kg, value) of the by-products kept by the mill."""
        self.ensure_one()
        company = self.company_id
        result = []
        for prefix, xml_id, policy in OUTPUTS:
            if policy and company[f'{policy}_policy'] == 'kept' and self[f'{prefix}_kg'] > 0:
                value = self.currency_id.round(self[f'{prefix}_kg'] * company[f'{policy}_value'])
                if value:
                    result.append((self.env.ref(xml_id), self[f'{prefix}_kg'], value))
        return result

    def _cl_byproduct_credit_invoice_lines(self):
        return [{
            'name': self.env._("%(product)s kept by the mill (%(kg)s kg)", product=product.name, kg=kg),
            'quantity': 1,
            'price_unit': -value,
            'account_id': product._get_product_accounts()['expense'].id,
            'tax_ids': [(6, 0, [])],
        } for product, kg, value in self._cl_byproduct_lines()]

    def _cl_post_byproduct_credit(self):
        """Credit the farmer for the by-products the mill keeps: Dr by-product cost / Cr proceeds payable."""
        self.ensure_one()
        company = self.company_id
        fs = self.farmer_season_id
        if not company.cl_rice_proceeds_account_id or not company.cl_rice_settlement_journal_id:
            raise UserError(self.env._("Set the farmer proceeds account and the settlement journal in the Rice Mill settings."))
        line_vals = []
        total = 0.0
        for product, kg, value in self._cl_byproduct_lines():
            total += value
            line_vals.append({
                'name': self.env._("%(product)s kept by the mill (%(kg)s kg) - %(job)s", product=product.name, kg=kg, job=self.name),
                'account_id': product._get_product_accounts()['expense'].id,
                'debit': value,
                'analytic_distribution': fs._cl_analytic_distribution(),
            })
        if not line_vals:
            return
        line_vals.append({
            'name': self.env._("By-products credit - %s", self.name),
            'account_id': company.cl_rice_proceeds_account_id.id,
            'partner_id': self.partner_id.id,
            'credit': total,
            'cl_farmer_season_id': fs.id,
        })
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': company.cl_rice_settlement_journal_id.id,
            'date': fields.Date.context_today(self),
            'ref': self.env._("By-products kept from %s", self.name),
            'cl_farmer_season_id': fs.id,
            'company_id': company.id,
            'line_ids': [(0, 0, vals) for vals in line_vals],
        })
        move.action_post()
        self.credit_move_id = move

    def _cl_register_walkin_payment(self, invoice):
        self.ensure_one()
        journal = self.payment_journal_id
        method_line = journal._get_available_payment_method_lines('inbound').filtered('payment_account_id')[:1]
        if not method_line:
            raise UserError(self.env._(
                "The journal %s has no outstanding receipts account on its incoming payment methods.",
                journal.display_name))
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': invoice.amount_total,
            'date': invoice.invoice_date,
            'journal_id': journal.id,
            'payment_method_line_id': method_line.id,
            'memo': invoice.name,
            'company_id': self.company_id.id,
        })
        payment.action_post()
        (payment.move_id.line_ids | invoice.line_ids).filtered(
            lambda l: l.account_id == invoice.partner_id.with_company(self.company_id).property_account_receivable_id
            and not l.reconciled
        ).reconcile()
        self.payment_id = payment

    def action_cancel(self):
        if self.filtered(lambda j: j.state == 'done'):
            raise UserError(self.env._("A finished milling job cannot be cancelled."))
        self.state = 'cancelled'

    def action_open_mill_lots(self):
        self.ensure_one()
        return {
            'name': self.env._("Lots"),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.lot',
            'view_mode': 'list,form',
            'domain': [('id', 'in', (self.input_lot_id | self.output_picking_id.move_line_ids.lot_id).ids)],
        }

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))
