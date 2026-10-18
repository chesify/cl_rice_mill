from odoo import api, fields, models

from .cl_rice_opening_balance import DEBT_TYPES


class ClRiceOpeningBalanceLine(models.Model):
    _name = 'cl.rice.opening.balance.line'
    _description = "Opening Balance of a Farmer"
    _order = 'batch_id, sequence, id'

    batch_id = fields.Many2one('cl.rice.opening.balance', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='batch_id.company_id', store=True)
    currency_id = fields.Many2one(related='batch_id.currency_id')
    sequence = fields.Integer(default=10)
    farmer_code = fields.Char()
    farmer_name = fields.Char()
    phone = fields.Char()
    nida = fields.Char(string="NIDA")
    village = fields.Char()
    acreage = fields.Float(digits=(16, 2), help="Acreage planted this season: the credit limit follows from it.")
    field_officer = fields.Char(string="Field Officer (login or name)")
    field_officer_id = fields.Many2one('res.users', string="Officer", compute='_compute_field_officer_id')
    warning = fields.Char(readonly=True, help="Not blocking: worth checking before posting.")
    partner_id = fields.Many2one('res.partner', string="Farmer", domain="[('is_farmer', '=', True)]")
    farmer_season_id = fields.Many2one('cl.rice.farmer.season', string="Farmer Season", readonly=True, index=True)
    loan_amount = fields.Monetary(string="Loan")
    tractor_amount = fields.Monetary(string="Tractor")
    harvester_amount = fields.Monetary(string="Harvester")
    operator_amount = fields.Monetary(string="Operators Paid")
    storage_amount = fields.Monetary(string="Storage")
    milling_amount = fields.Monetary(string="Milling")
    other_amount = fields.Monetary(string="Other")
    debt_total = fields.Monetary(string="Total Owed", compute='_compute_debt_total', store=True)
    paddy_bags = fields.Float(digits=(16, 1))
    paddy_kg = fields.Float(string="Paddy (kg)", digits='Product Unit')
    rice_bags = fields.Float(digits=(16, 1))
    rice_kg = fields.Float(string="Rice (kg)", digits='Product Unit')
    error = fields.Char(readonly=True)
    move_id = fields.Many2one('account.move', string="Opening Entry", readonly=True)
    picking_ids = fields.Many2many('stock.picking', string="Opening Stock", readonly=True)

    @api.depends(*[column for column, _account, _label in DEBT_TYPES])
    def _compute_debt_total(self):
        for line in self:
            line.debt_total = sum(line[column] for column, _account, _label in DEBT_TYPES)

    @api.depends('field_officer')
    def _compute_field_officer_id(self):
        Users = self.env['res.users'].sudo()
        for line in self:
            officer = Users
            if line.field_officer:
                value = line.field_officer.strip()
                officer = Users.search([('login', '=ilike', value), ('share', '=', False)], limit=1) \
                    or Users.search([('name', '=ilike', value), ('share', '=', False)], limit=1)
            line.field_officer_id = officer

    def _cl_find_farmer(self):
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        if self.partner_id:
            return self.partner_id
        if self.farmer_code:
            farmer = Partner.search([('cl_farmer_code', '=', self.farmer_code), ('is_farmer', '=', True)], limit=1)
            if farmer:
                return farmer
        if self.farmer_name:
            domain = [('name', '=ilike', self.farmer_name), ('is_farmer', '=', True)]
            if self.phone:
                domain.append(('phone', '=', self.phone))
            return Partner.search(domain, limit=1)
        return Partner

    def _cl_validate(self):
        posted_farmers = set(self.search([
            ('batch_id.state', '=', 'posted'),
            ('farmer_season_id.season_id', 'in', self.batch_id.season_id.ids),
        ]).farmer_season_id.partner_id.ids)
        seen = set()
        for line in self:
            errors = []
            farmer = line._cl_find_farmer()
            if farmer:
                line.partner_id = farmer
            elif not line.farmer_name:
                errors.append(self.env._("Farmer not found"))
            if farmer and farmer.cl_farmer_state == 'blocked':
                errors.append(self.env._("Farmer is blocked"))
            key = farmer.id if farmer else (line.farmer_name or '').strip().lower(), line.phone or ''
            if key in seen:
                errors.append(self.env._("Duplicate farmer in the file"))
            seen.add(key)
            duplicates = self.env['res.partner']._cl_find_duplicates(nida=line.nida, phone=line.phone, exclude=farmer)
            if duplicates['nida']:
                errors.append(self.env._("NIDA already used by %s", duplicates['nida'][0].name))
            if not farmer and duplicates['phone']:
                errors.append(self.env._(
                    "Phone already used by %(farmer)s (%(code)s): use their farmer code",
                    farmer=duplicates['phone'][0].name, code=duplicates['phone'][0].cl_farmer_code or '-'))
            if farmer and farmer.id in posted_farmers:
                errors.append(self.env._("Opening balance already posted for this farmer and season"))
            amounts = [line[column] for column, _account, _label in DEBT_TYPES]
            if any(amount < 0 for amount in amounts) or min(line.paddy_kg, line.rice_kg, line.paddy_bags, line.rice_bags) < 0:
                errors.append(self.env._("Negative amounts or weights"))
            if not line.debt_total and not line.paddy_kg and not line.rice_kg:
                errors.append(self.env._("Nothing to import"))
            if (line.paddy_bags and not line.paddy_kg) or (line.rice_bags and not line.rice_kg):
                errors.append(self.env._("Bags without kg"))
            if line.acreage < 0:
                errors.append(self.env._("Negative acreage"))
            if line.field_officer and not line.field_officer_id:
                errors.append(self.env._("Field officer %s not found (use their login)", line.field_officer))
            line.error = "; ".join(errors) if errors else False
            line.warning = line._cl_activity_warning(farmer)

    def _cl_activity_warning(self, farmer):
        """The farmer already has records this season: the opening balance may count them twice."""
        self.ensure_one()
        if not farmer:
            return False
        fs = self.env['cl.rice.farmer.season'].sudo().search(
            [('partner_id', '=', farmer.id), ('season_id', '=', self.batch_id.season_id.id)], limit=1)
        if not fs:
            return False
        activity = []
        loans = fs.loan_ids.filtered(lambda l: l.state in ('disbursed', 'settled'))
        if loans:
            activity.append(self.env._("%s loans", len(loans)))
        jobs = fs.service_job_ids.filtered(lambda j: j.state == 'charged')
        if jobs:
            activity.append(self.env._("%s service jobs", len(jobs)))
        intakes = self.env['cl.rice.intake'].sudo().search_count([('farmer_season_id', '=', fs.id), ('state', '=', 'done')])
        if intakes:
            activity.append(self.env._("%s intakes", intakes))
        payments = fs.payment_ids.filtered(lambda p: p.state == 'posted')
        if payments:
            activity.append(self.env._("%s payments", len(payments)))
        if not activity:
            return False
        return self.env._(
            "Already has activity this season (%s): check the amounts are not counted twice", ", ".join(activity))

    def _cl_post(self):
        self.ensure_one()
        batch = self.batch_id
        company = batch.company_id
        farmer = self._cl_find_farmer()
        if not farmer:
            farmer = self.env['res.partner'].sudo().create({
                'name': self.farmer_name,
                'is_farmer': True,
                'phone': self.phone,
                'cl_nida_number': self.nida or False,
                'cl_village': self.village,
                'cl_field_officer_id': self.field_officer_id.id,
                'company_id': company.id,
            })
        FarmerSeason = self.env['cl.rice.farmer.season'].sudo()
        fs = FarmerSeason.search([('partner_id', '=', farmer.id), ('season_id', '=', batch.season_id.id)], limit=1)
        if not fs:
            fs = FarmerSeason.create({
                'partner_id': farmer.id,
                'season_id': batch.season_id.id,
                'company_id': company.id,
                'acreage': self.acreage,
                'field_officer_id': (self.field_officer_id or farmer.cl_field_officer_id).id,
            })
        else:
            fs_vals = {}
            if self.acreage and not fs.acreage:
                fs_vals['acreage'] = self.acreage
            if self.field_officer_id and fs.field_officer_id != self.field_officer_id:
                fs_vals['field_officer_id'] = self.field_officer_id.id
            if fs_vals:
                fs.write(fs_vals)
        if self.field_officer_id and not farmer.cl_field_officer_id:
            farmer.cl_field_officer_id = self.field_officer_id
        if fs.state == 'draft':
            fs.action_activate()
        fs._cl_check_open()
        values = {'partner_id': farmer.id, 'farmer_season_id': fs.id}
        if self.debt_total:
            values['move_id'] = self._cl_post_debts(fs).id
        pickings = self.env['stock.picking']
        for role, kg, bags in (('paddy', self.paddy_kg, self.paddy_bags), ('rice', self.rice_kg, self.rice_bags)):
            if kg > 0:
                product = self.env.ref(f'cl_rice_mill.product_{role}')
                lot = fs._cl_get_lot(product)
                pickings |= self.env['stock.picking']._cl_rice_done_transfer(
                    company.cl_rice_warehouse_id.in_type_id,
                    farmer.property_stock_supplier,
                    company._cl_rice_location_for(product),
                    farmer,
                    [{'product': product, 'qty': kg, 'lot': lot, 'bags': bags}],
                    origin=batch.name,
                    date=batch.date,
                )
        values['picking_ids'] = [(6, 0, pickings.ids)]
        self.write(values)
        if pickings:
            fs._cl_advance_state(('active', 'harvested'), 'in_storage')

    def _cl_post_debts(self, fs):
        self.ensure_one()
        batch = self.batch_id
        company = batch.company_id
        label_base = self.env._("Opening balance %s", batch.name)
        line_vals = []
        for column, account_field, label in DEBT_TYPES:
            amount = self[column]
            if amount <= 0:
                continue
            line_vals.append((0, 0, {
                'name': f"{label_base} - {label}",
                'account_id': company[account_field].id,
                'partner_id': fs.partner_id.id,
                'debit': amount,
                'cl_farmer_season_id': fs.id,
            }))
        line_vals.append((0, 0, {
            'name': label_base,
            'account_id': company.get_unaffected_earnings_account().id,
            'credit': self.debt_total,
        }))
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': company.cl_rice_settlement_journal_id.id,
            'date': batch.date,
            'ref': f"{label_base} - {fs.partner_id.name}",
            'cl_farmer_season_id': fs.id,
            'company_id': company.id,
            'line_ids': line_vals,
        })
        move.action_post()
        return move

    def _cl_reverse(self, reason):
        self.ensure_one()
        company = self.company_id
        if self.move_id.state == 'posted':
            self.move_id.line_ids.remove_move_reconcile()
            self.move_id._reverse_moves(
                [{'ref': self.env._("Reversal of %(ref)s: %(reason)s", ref=self.move_id.name, reason=reason)}],
                cancel=True)
        fs = self.farmer_season_id
        for role, kg, bags in (('paddy', self.paddy_kg, self.paddy_bags), ('rice', self.rice_kg, self.rice_bags)):
            if kg <= 0:
                continue
            product = self.env.ref(f'cl_rice_mill.product_{role}')
            lot = fs._cl_find_lot(product)
            quants = self.env['stock.quant']._cl_owned_quants(product, lot, fs.partner_id, company._cl_rice_store_locations())
            self.env['stock.picking']._cl_rice_done_transfer(
                company.cl_rice_warehouse_id.out_type_id,
                quants[:1].location_id or company._cl_rice_location_for(product),
                fs.partner_id.property_stock_supplier,
                fs.partner_id,
                [{'product': product, 'qty': kg, 'lot': lot, 'bags': bags, 'quants': quants}],
                origin=self.env._("Reversal of %s", self.batch_id.name),
            )
