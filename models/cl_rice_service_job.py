from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .cl_rice_service_rate import SERVICE_ACCOUNTING, SERVICE_TYPES, SERVICE_UNITS

# storage and milling are charged by their own documents
JOB_SERVICE_TYPES = [item for item in SERVICE_TYPES if item[0] not in ('storage', 'milling')]


class ClRiceServiceJob(models.Model):
    _name = 'cl.rice.service.job'
    _description = "Service Job"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    farmer_season_id = fields.Many2one(
        'cl.rice.farmer.season', string="Farmer Season", required=True, index=True, tracking=True,
        check_company=True, domain="[('state', 'not in', ('draft', 'settled', 'closed'))]")
    partner_id = fields.Many2one(related='farmer_season_id.partner_id', store=True, string="Farmer", index=True)
    season_id = fields.Many2one(related='farmer_season_id.season_id', store=True, index=True)
    field_officer_id = fields.Many2one(related='farmer_season_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='farmer_season_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    state = fields.Selection(
        [('draft', "Draft"), ('done', "Done"), ('charged', "Charged"), ('cancelled', "Cancelled")],
        default='draft', required=True, tracking=True, copy=False, index=True)

    service_type = fields.Selection(JOB_SERVICE_TYPES, required=True, tracking=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    vehicle_id = fields.Many2one('fleet.vehicle', string="Machine", tracking=True, check_company=True)
    operator_id = fields.Many2one('hr.employee', string="Operator", tracking=True, check_company=True)
    rate_id = fields.Many2one(
        'cl.rice.service.rate', string="Rate", compute='_compute_rate_id', store=True, readonly=False,
        check_company=True, domain="[('service_type', '=', service_type)]")
    uom = fields.Selection(SERVICE_UNITS, string="Unit", compute='_compute_rate_id', store=True, readonly=False)
    quantity = fields.Float(digits='Product Unit', default=1.0, tracking=True)
    price_unit = fields.Monetary(
        string="Rate per Unit", compute='_compute_price_unit', store=True, readonly=False, tracking=True)
    is_free = fields.Boolean(
        string="Free of Charge", tracking=True,
        help="The service is given free: nothing is charged to the farmer but its cost is still recorded.")
    amount = fields.Monetary(compute='_compute_amount', store=True, tracking=True)

    # internal cost of the job (job margin and cost of free services; not posted)
    fuel_litres = fields.Float(string="Fuel Used (L)", digits=(16, 2))
    fuel_cost = fields.Monetary(
        compute='_compute_fuel_cost', store=True,
        help="Litres used x the fuel price of the settings. Posted to the machine when the job is charged.")
    operator_cost = fields.Monetary(
        compute='_compute_operator_cost', store=True, readonly=False,
        help="Operator pay for this job borne by the mill (quantity x operator pay of the rate).")
    cost_total = fields.Monetary(string="Job Cost", compute='_compute_amount', store=True)
    cost_move_id = fields.Many2one('account.move', string="Job Cost Entry", readonly=True, copy=False)

    # operator paid on behalf of the farmer
    operator_paid_amount = fields.Monetary(
        string="Paid to Operator on Farmer's Behalf", tracking=True,
        help="Amount the mill pays the operators for the farmer. It is added to what the farmer owes.")
    operator_partner_id = fields.Many2one(
        'res.partner', string="Paid To", compute='_compute_operator_partner_id', store=True, readonly=False)
    operator_payment_mode = fields.Selection(
        [('cash', "Paid now"), ('payable', "Owed to the operator (paid later)")],
        string="Operator Payment", default='cash')
    operator_journal_id = fields.Many2one(
        'account.journal', string="Paid From", check_company=True, domain="[('type', 'in', ('cash', 'bank'))]")

    invoice_id = fields.Many2one('account.move', string="Invoice", readonly=True, copy=False)
    operator_move_id = fields.Many2one('account.move', string="Operator Entry", readonly=True, copy=False)
    operator_payment_id = fields.Many2one('account.payment', string="Operator Payment Record", readonly=True, copy=False)
    note = fields.Text()
    photo = fields.Binary(attachment=True, help="Photo taken in the field.")
    source = fields.Selection(
        [('office', "Office"), ('field', "Field App")], default='office', readonly=True, copy=False)
    cl_client_uuid = fields.Char(
        string="Field App ID", readonly=True, copy=False,
        help="Identifier given by the field app; a record sent twice is only created once.")

    _cl_client_uuid_uniq = models.UniqueIndex(
        '(cl_client_uuid) WHERE cl_client_uuid IS NOT NULL', "This field record was already received.")
    _quantity_positive = models.Constraint('CHECK(quantity >= 0)', "The quantity cannot be negative.")
    _price_positive = models.Constraint('CHECK(price_unit >= 0)', "The rate cannot be negative.")
    _operator_positive = models.Constraint(
        'CHECK(operator_paid_amount >= 0 AND fuel_cost >= 0 AND operator_cost >= 0 AND fuel_litres >= 0)',
        "Costs and amounts cannot be negative.")

    @api.depends('service_type', 'date', 'company_id')
    def _compute_rate_id(self):
        Rate = self.env['cl.rice.service.rate']
        for job in self:
            if job.service_type and job.date and job.company_id:
                rate = Rate._cl_get_rate(job.service_type, job.date, job.company_id)
                job.rate_id = rate
                job.uom = rate.uom or job.uom
            else:
                job.rate_id = False

    @api.depends('rate_id')
    def _compute_price_unit(self):
        for job in self:
            job.price_unit = job.rate_id._cl_price('farmer') if job.rate_id else job.price_unit

    @api.depends('fuel_litres', 'company_id.cl_rice_fuel_price')
    def _compute_fuel_cost(self):
        for job in self:
            job.fuel_cost = job.currency_id.round(job.fuel_litres * job.company_id.cl_rice_fuel_price)

    @api.depends('quantity', 'rate_id.operator_rate')
    def _compute_operator_cost(self):
        for job in self:
            job.operator_cost = job.currency_id.round(job.quantity * job.rate_id.operator_rate)

    @api.depends('quantity', 'price_unit', 'is_free', 'fuel_cost', 'operator_cost')
    def _compute_amount(self):
        for job in self:
            job.amount = 0.0 if job.is_free else job.currency_id.round(job.quantity * job.price_unit)
            job.cost_total = job.fuel_cost + job.operator_cost

    @api.depends('operator_id')
    def _compute_operator_partner_id(self):
        for job in self:
            job.operator_partner_id = job.operator_id.work_contact_id or job.operator_partner_id

    @api.constrains('operator_paid_amount', 'operator_partner_id', 'operator_payment_mode', 'operator_journal_id')
    def _check_operator_payment(self):
        for job in self.filtered(lambda j: j.operator_paid_amount):
            if not job.operator_partner_id:
                raise ValidationError(self.env._("Set who the operator payment is made to."))
            if job.operator_payment_mode == 'cash' and not job.operator_journal_id:
                raise ValidationError(self.env._("Set the journal the operator is paid from."))

    @api.depends('company_id')
    def _compute_currency_id(self):
        # the company comes from the farmer season: use the current one until it is chosen
        for record in self:
            record.currency_id = record.company_id.currency_id or self.env.company.currency_id

    @api.model_create_multi
    def create(self, vals_list):
        FarmerSeason = self.env['cl.rice.farmer.season']
        for vals in vals_list:
            company = FarmerSeason.browse(vals.get('farmer_season_id')).company_id or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.service.job') or '/'
        jobs = super().create(vals_list)
        jobs.farmer_season_id._cl_check_open()
        return jobs

    def write(self, vals):
        frozen = {'fuel_litres', 'operator_cost', 'farmer_season_id', 'service_type', 'quantity', 'price_unit', 'is_free', 'operator_paid_amount',
                  'operator_partner_id', 'operator_payment_mode', 'operator_journal_id', 'rate_id'}
        if frozen & set(vals) and self.filtered(lambda j: j.state in ('charged', 'cancelled')):
            raise UserError(self.env._("A charged service job cannot be changed. Reverse it and record a new one."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda j: j.state not in ('draft', 'cancelled')):
            raise UserError(self.env._("Only draft or cancelled service jobs can be deleted."))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_done(self):
        for job in self:
            if job.state != 'draft':
                raise UserError(self.env._("Only draft jobs can be marked as done."))
            if not job.quantity:
                raise UserError(self.env._("Enter the quantity of job %s.", job.name))
            job.farmer_season_id._cl_check_open()
        self.state = 'done'

    def action_charge(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only a Rice Mill Accountant can charge service jobs."))
        for job in self:
            if job.state != 'done':
                raise UserError(self.env._("Only done jobs can be charged."))
            job.farmer_season_id._cl_check_open()
            if job.amount:
                job._cl_create_invoice()
            if job.operator_paid_amount:
                job._cl_pay_operator()
            job._cl_post_job_cost()
            job.state = 'charged'
            if job.amount or job.operator_paid_amount:
                job.partner_id._cl_rice_notify('service_charged', job)

    def _cl_analytic_distribution(self):
        self.ensure_one()
        accounts = self.env.ref(SERVICE_ACCOUNTING[self.service_type][1])
        if self.vehicle_id:
            accounts |= self.vehicle_id._cl_get_analytic_account()
        return self.farmer_season_id._cl_analytic_distribution(accounts)

    def _cl_income_account(self):
        self.ensure_one()
        account = self.company_id[SERVICE_ACCOUNTING[self.service_type][0]]
        if not account:
            raise UserError(self.env._("Set the income accounts in the Rice Mill settings."))
        return account

    def _cl_create_invoice(self):
        self.ensure_one()
        company = self.company_id
        if not company.cl_rice_charge_journal_id:
            raise UserError(self.env._("Set the farmer charges journal in the Rice Mill settings."))
        fs = self.farmer_season_id
        type_label = dict(self._fields['service_type']._description_selection(self.env))[self.service_type]
        invoice = self.env['account.move'].sudo().create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'journal_id': company.cl_rice_charge_journal_id.id,
            'invoice_date': self.date,
            'invoice_origin': self.name,
            'cl_farmer_season_id': fs.id,
            'company_id': company.id,
            'invoice_line_ids': [(0, 0, {
                'name': f"{type_label} - {self.name}",
                'quantity': self.quantity,
                'price_unit': self.price_unit,
                'account_id': self._cl_income_account().id,
                'tax_ids': [(6, 0, [])],
                'analytic_distribution': self._cl_analytic_distribution(),
                'vehicle_id': self.vehicle_id.id,
            })],
        })
        invoice.action_post()
        fs._cl_tag_receivable_lines(invoice)
        self.invoice_id = invoice

    def _cl_post_job_cost(self):
        """Book what the job cost the mill (fuel from the tank, operator wages) on the machine, the
        service line, the farmer season and the season project. Free jobs are costed too."""
        self.ensure_one()
        if not self.fuel_cost and not self.operator_cost:
            return
        company = self.company_id
        accounts = (company.cl_rice_fuel_clearing_account_id, company.cl_rice_machine_cost_account_id,
                    company.cl_rice_operator_wage_account_id, company.cl_rice_operator_accrual_account_id,
                    company.cl_rice_cost_journal_id)
        if not all(accounts):
            raise UserError(self.env._("Run the Rice Mill setup: the machine cost accounts are missing."))
        distribution = self._cl_analytic_distribution()
        label = self.env._("Cost of %(job)s - %(machine)s", job=self.name, machine=self.vehicle_id.display_name or '-')
        line_vals = []
        if self.fuel_cost:
            line_vals += [
                {'name': self.env._("Fuel %(litres)s L - %(label)s", litres=self.fuel_litres, label=label),
                 'account_id': company.cl_rice_machine_cost_account_id.id, 'debit': self.fuel_cost,
                 'analytic_distribution': distribution, 'vehicle_id': self.vehicle_id.id},
                {'name': label, 'account_id': company.cl_rice_fuel_clearing_account_id.id, 'credit': self.fuel_cost},
            ]
        if self.operator_cost:
            line_vals += [
                {'name': self.env._("Operator wages - %s", label),
                 'account_id': company.cl_rice_operator_wage_account_id.id, 'debit': self.operator_cost,
                 'analytic_distribution': distribution, 'vehicle_id': self.vehicle_id.id},
                {'name': label, 'account_id': company.cl_rice_operator_accrual_account_id.id,
                 'partner_id': self.operator_id.work_contact_id.id, 'credit': self.operator_cost},
            ]
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': company.cl_rice_cost_journal_id.id,
            'date': self.date,
            'ref': label,
            'company_id': company.id,
            'line_ids': [(0, 0, vals) for vals in line_vals],
        })
        move.action_post()
        self.cost_move_id = move

    def _cl_pay_operator(self):
        self.ensure_one()
        company = self.company_id
        fs = self.farmer_season_id
        payee = self.operator_partner_id
        payable_account = payee.with_company(company).property_account_payable_id
        if not company.cl_rice_service_account_id or not company.cl_rice_loan_journal_id:
            raise UserError(self.env._("Set the farmer service receivable and the loans journal in the Rice Mill settings."))
        label = self.env._("Operators paid for %(farmer)s - %(job)s", farmer=self.partner_id.name, job=self.name)
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': company.cl_rice_loan_journal_id.id,
            'date': self.date,
            'ref': label,
            'cl_farmer_season_id': fs.id,
            'company_id': company.id,
            'line_ids': [
                (0, 0, {
                    'name': label,
                    'account_id': company.cl_rice_service_account_id.id,
                    'partner_id': self.partner_id.id,
                    'debit': self.operator_paid_amount,
                    'cl_farmer_season_id': fs.id,
                }),
                (0, 0, {
                    'name': label,
                    'account_id': payable_account.id,
                    'partner_id': payee.id,
                    'credit': self.operator_paid_amount,
                }),
            ],
        })
        move.action_post()
        self.operator_move_id = move
        if self.operator_payment_mode != 'cash':
            return
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': payee.id,
            'amount': self.operator_paid_amount,
            'date': self.date,
            'journal_id': self.operator_journal_id.id,
            'payment_method_line_id': self.operator_journal_id._cl_outbound_method_line().id,
            'destination_account_id': payable_account.id,
            'memo': label,
            'company_id': company.id,
        })
        payment.action_post()
        (payment.move_id.line_ids | move.line_ids).filtered(
            lambda l: l.account_id == payable_account and not l.reconciled
        ).reconcile()
        self.operator_payment_id = payment

    def action_open_reverse(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Reverse Service Job"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_action': 'reverse',
            },
        }

    def action_cancel(self):
        for job in self:
            if job.state == 'charged':
                raise UserError(self.env._("A charged job can only be reversed by a manager, with a reason."))
        self.state = 'cancelled'

    def action_draft(self):
        if self.filtered(lambda j: j.state not in ('done', 'cancelled')):
            raise UserError(self.env._("Only done or cancelled jobs can be reset to draft."))
        self.state = 'draft'

    def _cl_reverse(self, reason, amount=None, date=None):
        self.ensure_one()
        self._cl_check_manager()
        if self.state != 'charged':
            raise UserError(self.env._("Only charged jobs can be reversed."))
        self.farmer_season_id._cl_check_open()
        moves = (self.invoice_id | self.operator_move_id | self.cost_move_id).filtered(lambda m: m.state == 'posted')
        if moves:
            moves.line_ids.remove_move_reconcile()
            reversals = moves._reverse_moves(
                [{'ref': self.env._("Reversal of %(ref)s: %(reason)s", ref=move.name, reason=reason)} for move in moves],
                cancel=True)
            self.farmer_season_id._cl_tag_receivable_lines(reversals)
        self.state = 'cancelled'
        body = self.env._("Service job reversed by %(user)s. Reason: %(reason)s", user=self.env.user.name, reason=reason)
        if self.operator_payment_id:
            body += " " + self.env._(
                "The cash already paid to %s stays as an advance on their account.", self.operator_partner_id.name)
        self.message_post(body=body)

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_reason_actions(self):
        return {'reverse': self._cl_reverse}
