from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ClRiceStockRelease(models.Model):
    _name = 'cl.rice.stock.release'
    _description = "Stock Release"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default=lambda self: self.env._("New"))
    sale_id = fields.Many2one(
        'cl.rice.facilitated.sale', string="Facilitated Sale", required=True, index=True, ondelete='cascade',
        check_company=True)
    farmer_season_id = fields.Many2one(related='sale_id.farmer_season_id', store=True, index=True)
    partner_id = fields.Many2one(related='sale_id.partner_id', store=True, string="Farmer", index=True)
    buyer_id = fields.Many2one(related='sale_id.buyer_id', store=True, string="Buyer")
    product_id = fields.Many2one(related='sale_id.product_id', store=True)
    lot_id = fields.Many2one(related='sale_id.lot_id', store=True)
    payment_route = fields.Selection(related='sale_id.payment_route')
    field_officer_id = fields.Many2one(related='sale_id.field_officer_id', store=True, index=True)
    company_id = fields.Many2one(related='sale_id.company_id', store=True, index=True, precompute=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    quantity = fields.Float(string="Quantity (kg)", digits='Product Unit', required=True, tracking=True)
    bags = fields.Float(digits=(16, 1), tracking=True)
    vehicle_plate = fields.Char(string="Vehicle Plate", tracking=True)
    driver_name = fields.Char(string="Driver", tracking=True)
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('cleared', "Cleared for Release"),
            ('released', "Released"),
            ('cancelled', "Cancelled"),
        ],
        default='draft', required=True, tracking=True, copy=False, index=True)
    clearance_basis = fields.Selection(
        [
            ('no_balance', "The farmer owes nothing"),
            ('funds', "Balance paid from the buyer's payment"),
            ('override', "Manager override"),
        ],
        readonly=True, copy=False, tracking=True)
    balance_at_clearance = fields.Monetary(string="Balance When Cleared", readonly=True, copy=False)
    funds_applied = fields.Monetary(string="Buyer Money Applied", readonly=True, copy=False)
    override_reason = fields.Text(readonly=True, copy=False)
    override_promised_date = fields.Date(string="Promised Payment", readonly=True, copy=False, tracking=True)
    cleared_uid = fields.Many2one('res.users', string="Authorised By", readonly=True, copy=False)
    cleared_date = fields.Datetime(string="Authorised On", readonly=True, copy=False)
    released_uid = fields.Many2one('res.users', string="Released By", readonly=True, copy=False)
    released_date = fields.Datetime(string="Released On", readonly=True, copy=False)
    picking_id = fields.Many2one('stock.picking', string="Transfer", readonly=True, copy=False)

    _quantity_positive = models.Constraint('CHECK(quantity > 0 AND bags >= 0)', "The quantity must be positive.")

    @api.depends('company_id')
    def _compute_currency_id(self):
        # the company comes from the farmer season: use the current one until it is chosen
        for record in self:
            record.currency_id = record.company_id.currency_id or self.env.company.currency_id

    @api.model_create_multi
    def create(self, vals_list):
        Sale = self.env['cl.rice.facilitated.sale']
        for vals in vals_list:
            company = Sale.browse(vals.get('sale_id')).company_id or self.env.company
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('cl.rice.stock.release') or '/'
        releases = super().create(vals_list)
        releases.farmer_season_id._cl_check_open_for_release()
        return releases

    def write(self, vals):
        if {'quantity', 'sale_id'} & set(vals) and self.filtered(lambda r: r.state != 'draft'):
            raise UserError(self.env._("A cleared release cannot be changed. Cancel it and prepare a new one."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if self.filtered(lambda r: r.state == 'released'):
            raise UserError(self.env._("A release that left the warehouse cannot be deleted."))

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------
    def _cl_check_quantity(self):
        """The release can never exceed what the farmer still holds (or what is left to release)."""
        self.ensure_one()
        sale = self.sale_id
        uom = self.product_id.uom_id
        if not sale.lot_id:
            raise UserError(self.env._("The farmer holds no %s this season.", self.product_id.name))
        others = self.search([
            ('lot_id', '=', sale.lot_id.id),
            ('state', '=', 'cleared'),
            ('id', '!=', self.id),
        ])
        available = sale.available_qty - sum(others.mapped('quantity'))
        if uom.compare(self.quantity, available) > 0:
            raise UserError(self.env._(
                "The farmer only holds %(available)s kg in lot %(lot)s that is not already cleared: "
                "%(qty)s kg cannot be released.",
                available=round(max(available, 0.0), 2), lot=sale.lot_id.name, qty=self.quantity))
        pending = sum(sale.release_ids.filtered(
            lambda r: r.state in ('cleared', 'released') and r != self).mapped('quantity'))
        if uom.compare(self.quantity + pending, sale.quantity) > 0:
            raise UserError(self.env._(
                "Only %s kg is left to release on this sale.", round(max(sale.quantity - pending, 0.0), 2)))

    def _cl_check_can_clear(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(self.env._("Release %s is already processed.", self.name))
        if self.sale_id.state in ('done', 'cancelled'):
            raise UserError(self.env._("The sale is closed."))
        self.farmer_season_id._cl_check_open_for_release()
        self._cl_check_quantity()

    # ------------------------------------------------------------------
    # Clearance
    # ------------------------------------------------------------------
    def action_clear(self):
        """Clear the release if the farmer's balance allows it, else explain why it is blocked."""
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_warehouse') \
                and not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_accountant'):
            raise AccessError(self.env._("Only the warehouse or the office can clear stock releases."))
        for release in self:
            release._cl_check_can_clear()
            fs = release.farmer_season_id
            owed, due = fs._cl_open_balances()
            balance = owed.get(fs.id, 0.0)
            funds = due.get(fs.id, 0.0)
            values = {'balance_at_clearance': balance}
            if release.currency_id.compare_amounts(balance, 0) <= 0:
                values['clearance_basis'] = 'no_balance'
            elif release.payment_route == 'luvanda' and release.currency_id.compare_amounts(funds, balance) >= 0:
                # the buyer's money held by the mill pays the farmer's balance first
                values['funds_applied'] = fs.sudo()._cl_apply_funds_held()
                values['clearance_basis'] = 'funds'
            else:
                raise UserError(self.env._(
                    "Release blocked: %(farmer)s still owes %(balance)s. The farmer must pay his balance first%(route)s, "
                    "or a manager can override the block with a reason.",
                    farmer=release.partner_id.name,
                    balance=release.currency_id.format(balance),
                    route=self.env._(", or the buyer must pay the mill") if release.payment_route == 'luvanda' else ""))
            release._cl_mark_cleared(values)

    def _cl_mark_cleared(self, values):
        self.ensure_one()
        values.update({'state': 'cleared', 'cleared_uid': self.env.uid, 'cleared_date': fields.Datetime.now()})
        self.write(values)
        self.sale_id._cl_update_state()
        basis = dict(self._fields['clearance_basis']._description_selection(self.env))[self.clearance_basis]
        self.message_post(body=self.env._(
            "Release of %(qty)s kg cleared by %(user)s: %(basis)s.", qty=self.quantity, user=self.env.user.name,
            basis=basis))
        self.partner_id._cl_rice_notify('release_cleared', self)

    def action_open_override(self):
        self.ensure_one()
        self._cl_check_manager()
        return {
            'name': self.env._("Override the Release Block"),
            'type': 'ir.actions.act_window',
            'res_model': 'cl.rice.reason',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_action': 'override'},
        }

    def _cl_override(self, reason, amount=None, date=None):
        """Manager override: clear the release although the farmer still owes; the promised payment
        is followed up by the office."""
        self.ensure_one()
        self._cl_check_manager()
        if not (reason or '').strip():
            raise UserError(self.env._("A reason is required to override the release block."))
        if not date:
            raise UserError(self.env._("Enter the date the farmer promised to pay."))
        self._cl_check_can_clear()
        balance = self.farmer_season_id._cl_balance_owed()
        self.write({'override_reason': reason, 'override_promised_date': date})
        self._cl_schedule_follow_up(balance, date)
        self._cl_mark_cleared({'balance_at_clearance': balance, 'clearance_basis': 'override'})
        message = self.env._(
            "Release block overridden by %(user)s while the farmer owes %(balance)s. Reason: %(reason)s",
            user=self.env.user.name, balance=self.currency_id.format(balance), reason=reason)
        self.message_post(body=message)
        self.farmer_season_id.message_post(body=message)

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------
    def action_release(self):
        """The stock leaves the warehouse with the buyer (owner stays the farmer: no valuation)."""
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_warehouse'):
            raise AccessError(self.env._("Only the warehouse can release stock."))
        for release in self:
            if release.state != 'cleared':
                raise UserError(self.env._("Release %s must be cleared first.", release.name))
            release.farmer_season_id._cl_check_open_for_release()
            release._cl_check_quantity()
            release._cl_move_stock()
            release.sale_id._cl_update_state()
            release.farmer_season_id._cl_advance_state(('harvested', 'in_storage'), 'released')

    def _cl_move_stock(self):
        self.ensure_one()
        company = self.company_id
        farmer = self.partner_id
        quants = self.env['stock.quant']._cl_owned_quants(
            self.product_id, self.lot_id, farmer, company._cl_rice_store_locations())
        source = quants[:1].location_id or company._cl_rice_location_for(self.product_id)
        picking = self.env['stock.picking']._cl_rice_done_transfer(
            company.cl_rice_release_type_id,
            source,
            company.cl_rice_released_location_id,
            farmer,
            [{'product': self.product_id, 'qty': self.quantity, 'lot': self.lot_id, 'bags': self.bags, 'quants': quants}],
            origin=self.name,
            partner=self.buyer_id,
        )
        self.write({
            'state': 'released',
            'picking_id': picking.id,
            'released_uid': self.env.uid,
            'released_date': fields.Datetime.now(),
        })
        self.message_post(body=self.env._(
            "%(qty)s kg (%(bags)s bags) released to %(buyer)s, vehicle %(plate)s.",
            qty=self.quantity, bags=self.bags, buyer=self.buyer_id.name, plate=self.vehicle_plate or '-'))

    def action_cancel(self):
        if self.filtered(lambda r: r.state == 'released'):
            raise UserError(self.env._("Stock that left the warehouse cannot be cancelled."))
        self.state = 'cancelled'
        self.sale_id._cl_update_state()

    def action_print_gate_pass(self):
        self.ensure_one()
        self._cl_check_gate_pass()
        return self.env.ref('cl_rice_mill.action_report_gate_pass').report_action(self)

    def _cl_check_gate_pass(self):
        not_cleared = self.filtered(lambda r: r.state not in ('cleared', 'released'))
        if not_cleared:
            raise UserError(self.env._("The gate pass can only be printed for a cleared release (%s).", not_cleared[0].name))

    def _cl_schedule_follow_up(self, balance, date):
        """Remind the office to collect the promised payment on the promised date."""
        self.ensure_one()
        accountants = self.env.ref('cl_rice_mill.cl_rice_mill_group_accountant').all_user_ids.filtered(
            lambda u: not u.share and u.company_id == self.company_id)
        managers = self.env.ref('cl_rice_mill.cl_rice_mill_group_manager').all_user_ids
        user = (accountants - managers)[:1] or accountants[:1] or self.env.user
        self.farmer_season_id.sudo().activity_schedule(
            'mail.mail_activity_data_todo',
            date_deadline=date,
            user_id=user.id,
            summary=self.env._("Collect %s promised after a release override", self.currency_id.format(balance)),
            note=self.env._(
                "Stock was released on %(release)s under a manager override while %(farmer)s owed %(balance)s. "
                "He promised to pay by %(date)s.",
                release=self.name, farmer=self.partner_id.name, balance=self.currency_id.format(balance), date=date),
        )

    def _cl_check_manager(self):
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can do this."))

    def _cl_reason_actions(self):
        return {'override': self._cl_override}
