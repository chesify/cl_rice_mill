import base64
import io
import json
import time
from collections import defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import SQL

# results of a section, per (database, user, companies, section, filters): (time, data)
_CACHE = {}
CACHE_SECONDS = 300

SECTIONS = ['kpi', 'income', 'farmers', 'stock', 'milling', 'machines', 'field', 'alerts']
ROLE_SECTIONS = {
    'manager': SECTIONS,
    'accountant': ['kpi', 'income', 'farmers', 'alerts'],
    'warehouse': ['kpi', 'stock', 'milling'],
    'officer': ['farmers', 'field'],
}
ROLE_KPIS = {
    'manager': None,  # all
    'accountant': None,
    'warehouse': ['paddy', 'rice', 'active'],
}

# company income account field -> (key, label)
INCOME_LINES = [
    ('cl_rice_tractor_income_account_id', 'tractor', "Tractor"),
    ('cl_rice_harvester_income_account_id', 'harvester', "Harvester"),
    ('cl_rice_other_income_account_id', 'transport', "Transport and other"),
    ('cl_rice_storage_income_account_id', 'storage', "Storage"),
    ('cl_rice_milling_income_account_id', 'milling', "Milling"),
    ('cl_rice_byproduct_income_account_id', 'byproducts', "Bran and husk sales"),
]
# company cost account field -> (key, label)
COST_LINES = [
    ('cl_rice_machine_cost_account_id', 'fuel', "Fuel"),
    ('cl_rice_repair_account_id', 'repairs', "Repairs"),
    ('cl_rice_operator_wage_account_id', 'wages', "Operator wages"),
    ('cl_rice_transport_account_id', 'transport', "Staff transport"),
    ('cl_rice_bags_account_id', 'small', "Small expenses"),
    ('cl_rice_airtime_account_id', 'small', "Small expenses"),
    ('cl_rice_office_account_id', 'small', "Small expenses"),
]
SERVICE_INCOME = {
    'ploughing': 'cl_rice_tractor_income_account_id',
    'harrowing': 'cl_rice_tractor_income_account_id',
    'harvesting': 'cl_rice_harvester_income_account_id',
    'transport': 'cl_rice_other_income_account_id',
    'bagging': 'cl_rice_other_income_account_id',
    'other': 'cl_rice_other_income_account_id',
    'milling': 'cl_rice_milling_income_account_id',
}


class ClRiceDashboard(models.AbstractModel):
    _name = 'cl.rice.dashboard'
    _description = "Rice Mill Dashboard"

    # ------------------------------------------------------------------
    # Roles, filters, cache
    # ------------------------------------------------------------------
    @api.model
    def _cl_role(self):
        user = self.env.user
        for role, group in (
            ('manager', 'cl_rice_mill.cl_rice_mill_group_manager'),
            ('accountant', 'cl_rice_mill.cl_rice_mill_group_accountant'),
            ('warehouse', 'cl_rice_mill.cl_rice_mill_group_warehouse'),
            ('warehouse', 'cl_rice_mill.cl_rice_mill_group_mill_operator'),
            ('officer', 'cl_rice_mill.cl_rice_mill_group_field_officer'),
        ):
            if user.has_group(group):
                return role
        raise AccessError(self.env._("The dashboard is for rice mill staff."))

    @api.model
    def get_dashboard_meta(self):
        """Everything the dashboard needs before loading the sections: role, filter choices, saved filters."""
        role = self._cl_role()
        company = self.env.company
        seasons = self.env['cl.rice.season'].search([('company_id', '=', company.id)])
        current = self.env['cl.rice.season']._cl_current(company)
        officers = self.env['res.users'].search([
            ('share', '=', False), ('group_ids', 'in', self.env.ref('cl_rice_mill.cl_rice_mill_group_field_officer').ids)])
        partners = self.env['res.partner'].search([('is_farmer', '=', True)])
        villages = sorted({v for v in partners.mapped('cl_village') if v})
        machines = self.env['fleet.vehicle'].search([('cl_machine_category', '!=', False)])
        service_types = self.env['cl.rice.service.rate']._fields['service_type']._description_selection(self.env)
        settings = self.env['res.users.settings']._find_or_create_for_user(self.env.user)
        return {
            'role': role,
            'sections': ROLE_SECTIONS[role],
            'currency': {'symbol': company.currency_id.symbol, 'position': company.currency_id.position,
                         'name': company.currency_id.name},
            'default_season_id': current.id,
            'seasons': [{'id': s.id, 'name': s.name, 'date_start': fields.Date.to_string(s.date_start),
                         'date_end': fields.Date.to_string(s.date_end)} for s in seasons],
            'officers': [{'id': u.id, 'name': u.name} for u in officers] if role != 'officer' else [],
            'groups': [{'id': g.id, 'name': g.name} for g in self.env['cl.rice.farmer.group'].search([])],
            'villages': villages,
            'machines': [{'id': m.id, 'name': m.display_name} for m in machines],
            'service_types': [{'id': key, 'name': label} for key, label in service_types],
            'saved_filters': settings.sudo().cl_rice_dashboard_filters or {},
            'today': fields.Date.to_string(fields.Date.context_today(self)),
        }

    @api.model
    def save_dashboard_filters(self, filters):
        settings = self.env['res.users.settings']._find_or_create_for_user(self.env.user)
        settings.sudo().cl_rice_dashboard_filters = filters or {}
        return True

    @api.model
    def get_dashboard_data(self, section, filters=None, force=False):
        """One section of the dashboard for the filters, cached for 5 minutes per user and filters."""
        role = self._cl_role()
        if section not in ROLE_SECTIONS[role]:
            raise AccessError(self.env._("This part of the dashboard is not available to you."))
        filters = filters or {}
        key = (self.env.cr.dbname, self.env.uid, tuple(self.env.companies.ids), section,
               json.dumps(filters, sort_keys=True, default=str))
        cached = _CACHE.get(key)
        now = time.time()
        if cached and not force and now - cached[0] < CACHE_SECONDS:
            return dict(cached[1], cached=True)
        ctx = self._cl_context(filters, role)
        data = getattr(self, f'_cl_section_{section}')(ctx)
        data['updated'] = fields.Datetime.to_string(fields.Datetime.now())
        _CACHE[key] = (now, data)
        if len(_CACHE) > 500:
            for old in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:250]:
                _CACHE.pop(old, None)
        return dict(data, cached=False)

    @api.model
    def _cl_clear_cache(self):
        _CACHE.clear()

    @api.model
    def _cl_context(self, filters, role):
        """Normalised filters, the farmer seasons they cover (with the user's rights) and the date range."""
        company = self.env.company
        Season = self.env['cl.rice.season']
        season = Season.browse(int(filters['season_id'])).exists() if filters.get('season_id') else Season
        season = season or Season._cl_current(company)
        today = fields.Date.context_today(self)
        date_from = fields.Date.to_date(filters.get('date_from')) or season.date_start or today.replace(month=1, day=1)
        date_to = fields.Date.to_date(filters.get('date_to')) or min(season.date_end or today, today)
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        fs_domain = Domain('company_id', '=', company.id)
        if season:
            fs_domain &= Domain('season_id', '=', season.id)
        farmer_filter = role == 'officer'
        if filters.get('field_officer_id') and role != 'officer':
            fs_domain &= Domain('field_officer_id', '=', int(filters['field_officer_id']))
            farmer_filter = True
        if filters.get('farmer_group_id'):
            fs_domain &= Domain('partner_id.cl_farmer_group_id', '=', int(filters['farmer_group_id']))
            farmer_filter = True
        if filters.get('village'):
            fs_domain &= Domain('partner_id.cl_village', '=ilike', filters['village']) | Domain(
                'partner_id.cl_ward', '=ilike', filters['village'])
            farmer_filter = True
        # with the user's rights: a field officer only gets the farmers assigned to them
        farmer_seasons = self.env['cl.rice.farmer.season'].search(fs_domain)
        length = (date_to - date_from).days + 1
        previous = Season.search(
            [('company_id', '=', company.id), ('date_start', '<', season.date_start)], order='date_start desc',
            limit=1) if season else Season
        return {
            'company': company,
            'role': role,
            'season': season,
            'previous_season': previous,
            'date_from': date_from,
            'date_to': date_to,
            'prev_from': date_from - timedelta(days=length),
            'prev_to': date_from - timedelta(days=1),
            'today': today,
            'fs': farmer_seasons,
            'farmer_filter': farmer_filter,
            'vehicle_id': int(filters['vehicle_id']) if filters.get('vehicle_id') else False,
            'service_type': filters.get('service_type') or False,
            'compare': bool(filters.get('compare')),
        }

    # ------------------------------------------------------------------
    # Shared queries
    # ------------------------------------------------------------------
    def _act(self, model, domain, name, views=('list', 'form'), context=None):
        """A drill-down: the client opens these records."""
        return {'model': model, 'domain': self._cl_jsonable(list(Domain(domain))), 'name': name,
                'views': list(views), 'context': context or {}}

    def _cl_jsonable(self, value):
        if isinstance(value, (list, tuple)):
            return [self._cl_jsonable(v) for v in value]
        if isinstance(value, date):
            return fields.Date.to_string(value)
        if isinstance(value, models.BaseModel):
            return value.ids
        return value

    def _cl_accounts(self, ctx, specs, keys=None):
        """{account: key} of the company accounts in ``specs`` (optionally only some keys)."""
        company = ctx['company']
        result = {}
        for field_name, key, _label in specs:
            account = company[field_name]
            if account and (keys is None or key in keys):
                result[account] = key
        return result

    def _cl_line_domain(self, ctx, accounts, date_from=None, date_to=None):
        domain = Domain([
            ('company_id', '=', ctx['company'].id),
            ('parent_state', '=', 'posted'),
            ('account_id', 'in', [a.id for a in accounts]),
        ])
        if date_from:
            domain &= Domain('date', '>=', date_from)
        if date_to:
            domain &= Domain('date', '<=', date_to)
        if ctx['farmer_filter']:
            domain &= Domain('move_id.cl_farmer_season_id', 'in', ctx['fs'].ids)
        if ctx['vehicle_id']:
            domain &= Domain('vehicle_id', '=', ctx['vehicle_id'])
        return domain

    def _cl_income_accounts(self, ctx):
        accounts = self._cl_accounts(ctx, INCOME_LINES)
        if ctx['service_type']:
            wanted = ctx['company'][SERVICE_INCOME.get(ctx['service_type'], 'cl_rice_other_income_account_id')]
            accounts = {a: k for a, k in accounts.items() if a == wanted}
        return accounts

    def _cl_sum_lines(self, ctx, accounts, date_from, date_to, sign=1.0, groupby=()):
        if not accounts:
            return {} if groupby else 0.0
        groups = self.env['account.move.line'].sudo()._read_group(
            self._cl_line_domain(ctx, accounts, date_from, date_to), list(groupby), ['balance:sum'])
        if not groupby:
            return sign * (groups[0][0] or 0.0)
        return {tuple(group[:-1]) if len(groupby) > 1 else group[0]: sign * group[-1] for group in groups}

    def _cl_income(self, ctx, date_from, date_to):
        return self._cl_sum_lines(ctx, self._cl_income_accounts(ctx), date_from, date_to, sign=-1.0)

    def _cl_costs(self, ctx, date_from, date_to):
        if ctx['service_type']:
            return 0.0
        return self._cl_sum_lines(ctx, self._cl_accounts(ctx, COST_LINES), date_from, date_to)

    def _cl_months(self, ctx, count=6):
        end = ctx['date_to'].replace(day=1)
        return [end - relativedelta(months=index) for index in range(count - 1, -1, -1)]

    def _cl_monthly(self, values_by_month, months):
        return [round(values_by_month.get(month, 0.0), 2) for month in months]

    def _cl_open_items(self, ctx, account_fields, groupby=(), extra=None):
        """Open (residual) amounts of the farmer seasons on the given company accounts."""
        company = ctx['company']
        accounts = [company[f].id for f in account_fields if company[f]]
        if not accounts or not ctx['fs']:
            return {} if groupby else 0.0
        domain = Domain([
            ('company_id', '=', company.id),
            ('parent_state', '=', 'posted'),
            ('account_id', 'in', accounts),
            ('cl_farmer_season_id', 'in', ctx['fs'].ids),
            ('reconciled', '=', False),
        ])
        if extra:
            domain &= Domain(extra)
        self.env['account.move.line'].flush_model()
        groups = self.env['account.move.line'].sudo()._read_group(domain, list(groupby), ['amount_residual:sum'])
        if not groupby:
            return groups[0][0] or 0.0
        return {group[0]: group[-1] for group in groups}

    def _cl_store_quants(self, ctx, groupby, extra=None):
        company = ctx['company']
        locations = company._cl_rice_store_locations()
        if not locations:
            return []
        domain = Domain([
            ('location_id', 'child_of', locations.ids),
            ('product_id.cl_rice_product', '!=', False),
            ('quantity', '>', 0),
        ])
        if ctx['farmer_filter']:
            domain &= Domain('lot_id.cl_farmer_season_id', 'in', ctx['fs'].ids)
        if extra:
            domain &= Domain(extra)
        return self.env['stock.quant'].sudo()._read_group(domain, list(groupby), ['quantity:sum'])

    def _cl_lot_bags(self, lots):
        """Bags still held per lot (bags in minus bags out of the stores), in two grouped queries."""
        if not lots:
            return {}
        MoveLine = self.env['stock.move.line'].sudo()
        base = [('lot_id', 'in', lots.ids), ('state', '=', 'done')]
        into = MoveLine._read_group(
            base + [('location_dest_id.usage', '=', 'internal'), ('location_id.usage', '!=', 'internal')],
            ['lot_id'], ['cl_bags:sum'])
        out = MoveLine._read_group(
            base + [('location_id.usage', '=', 'internal'), ('location_dest_id.usage', '!=', 'internal')],
            ['lot_id'], ['cl_bags:sum'])
        bags = defaultdict(float)
        for lot, value in into:
            bags[lot] += value
        for lot, value in out:
            bags[lot] -= value
        return {lot: max(value, 0.0) for lot, value in bags.items()}

    def _cl_farmer_stock(self, ctx):
        """Farmer-owned kg and bags per product role, and kg per farmer season."""
        company_partner = ctx['company'].partner_id
        groups = self._cl_store_quants(ctx, ['lot_id', 'owner_id'])
        kg = defaultdict(float)
        bags = defaultdict(float)
        per_fs = defaultdict(lambda: defaultdict(float))
        lots = self.env['stock.lot'].concat(lot for lot, owner, _qty in groups if owner and owner != company_partner)
        lot_bags = self._cl_lot_bags(lots)
        for lot, owner, qty in groups:
            if not owner or owner == company_partner:
                continue
            role = lot.product_id.cl_rice_product
            role = 'rice' if role in ('rice', 'broken') else role
            kg[role] += qty
            bags[role] += lot_bags.get(lot, 0.0)
            if lot.cl_farmer_season_id:
                per_fs[lot.cl_farmer_season_id][role] += qty
        return kg, bags, per_fs

    def _cl_stock_value(self, ctx, kg_by_role, season=None):
        season = season or ctx['season']
        return (kg_by_role.get('paddy', 0.0) * season.reference_price
                + kg_by_role.get('rice', 0.0) * season.rice_reference_price)

    def _cl_kpi(self, key, label, value, fmt='money', previous=None, spark=None, action=None, sub=None, band=None):
        trend = None
        if previous is not None and previous:
            trend = round((value - previous) / abs(previous) * 100.0, 1)
        elif previous is not None and value:
            trend = 100.0
        return {'key': key, 'label': label, 'value': value, 'format': fmt, 'trend': trend, 'spark': spark or [],
                'action': action, 'sub': sub, 'band': band}

    # ------------------------------------------------------------------
    # KPI strip
    # ------------------------------------------------------------------
    def _cl_section_kpi(self, ctx):
        _ = self.env._
        company = ctx['company']
        d_from, d_to, months = ctx['date_from'], ctx['date_to'], self._cl_months(ctx)
        allowed = ROLE_KPIS.get(ctx['role'])
        kpis = []

        def wanted(key):
            return allowed is None or key in allowed

        income_accounts = self._cl_income_accounts(ctx)
        if wanted('income'):
            income = self._cl_income(ctx, d_from, d_to)
            costs = self._cl_costs(ctx, d_from, d_to)
            prev_income = self._cl_income(ctx, ctx['prev_from'], ctx['prev_to'])
            prev_costs = self._cl_costs(ctx, ctx['prev_from'], ctx['prev_to'])
            start = months[0]
            income_months = self._cl_sum_lines(ctx, income_accounts, start, d_to, -1.0, ['date:month'])
            cost_months = self._cl_sum_lines(ctx, self._cl_accounts(ctx, COST_LINES), start, d_to, 1.0, ['date:month'])
            profit = income - costs
            kpis.append(self._cl_kpi(
                'income', _("Total income"), income, previous=prev_income,
                spark=self._cl_monthly(income_months, months),
                action=self._act('account.move.line', self._cl_line_domain(ctx, income_accounts, d_from, d_to),
                                 _("Income"))))
            kpis.append(self._cl_kpi(
                'profit', _("Profit"), profit, previous=prev_income - prev_costs,
                spark=[i - c for i, c in zip(self._cl_monthly(income_months, months),
                                             self._cl_monthly(cost_months, months))],
                sub=_("Margin %s%%", round(profit / income * 100.0, 1) if income else 0),
                action=self._act('account.move.line', self._cl_line_domain(
                    ctx, list(income_accounts) + list(self._cl_accounts(ctx, COST_LINES)), d_from, d_to),
                    _("Income and costs"))))

        loan_domain = Domain([('farmer_season_id', 'in', ctx['fs'].ids), ('state', 'in', ('disbursed', 'settled')),
                              ('loan_type', '!=', 'carry_forward')])
        if wanted('loans'):
            Loan = self.env['cl.rice.loan']
            disbursed = Loan._read_group(loan_domain, [], ['amount:sum'])[0][0] or 0.0
            prev = Loan._read_group(loan_domain & Domain('disbursement_date', '>=', ctx['prev_from'])
                                    & Domain('disbursement_date', '<=', ctx['prev_to']), [], ['amount:sum'])[0][0]
            loan_months = dict(Loan._read_group(loan_domain & Domain('disbursement_date', '>=', months[0]),
                                                ['disbursement_date:month'], ['amount:sum']))
            outstanding = self._cl_open_items(ctx, ['cl_rice_loan_account_id'])
            recovered = (disbursed - outstanding) / disbursed * 100.0 if disbursed else 0.0
            kpis.append(self._cl_kpi(
                'loans', _("Loans disbursed"), disbursed,
                previous=prev or 0.0, spark=self._cl_monthly(loan_months, months),
                sub=_("Outstanding %(amount)s - recovered %(pct)s%%", amount=company.currency_id.format(outstanding),
                      pct=round(max(recovered, 0.0), 1)),
                action=self._act('cl.rice.loan', loan_domain, _("Loans"))))

        if wanted('owed'):
            owed = self._cl_open_items(ctx, ['cl_rice_loan_account_id', 'cl_rice_service_account_id'])
            past_due = self._cl_open_items(ctx, ['cl_rice_loan_account_id', 'cl_rice_service_account_id'],
                                           extra=[('date', '<', ctx['today'] - timedelta(days=60))])
            kpis.append(self._cl_kpi(
                'owed', _("Owed by farmers"), owed,
                sub=_("Past due (over 60 days) %s", company.currency_id.format(past_due)),
                action=self._act('cl.rice.farmer.season', [('id', 'in', ctx['fs'].ids)], _("Farmer Seasons"))))
        if wanted('funds'):
            funds = -self._cl_open_items(ctx, ['cl_rice_funds_held_account_id'])
            kpis.append(self._cl_kpi(
                'funds', _("Funds held for farmers"), funds,
                action=self._act('cl.rice.farmer.payment', [('farmer_season_id', 'in', ctx['fs'].ids),
                                                            ('state', '=', 'posted')], _("Payments Received"))))

        kg, bags, _per_fs = self._cl_farmer_stock(ctx)
        lot_action = self._act('stock.quant', [
            ('location_id', 'child_of', company._cl_rice_store_locations().ids),
            ('product_id.cl_rice_product', 'in', ('paddy',)), ('quantity', '>', 0)], _("Paddy in Store"))
        if wanted('paddy'):
            kpis.append(self._cl_kpi('paddy', _("Paddy in store"), kg.get('paddy', 0.0), fmt='kg',
                                     sub=_("%s bags", round(bags.get('paddy', 0.0))), action=lot_action))
        if wanted('rice'):
            rice_action = dict(lot_action, name=_("Milled Rice in Store"), domain=self._cl_jsonable([
                ('location_id', 'child_of', company._cl_rice_store_locations().ids),
                ('product_id.cl_rice_product', 'in', ('rice', 'broken')), ('quantity', '>', 0)]))
            kpis.append(self._cl_kpi('rice', _("Milled rice in store"), kg.get('rice', 0.0), fmt='kg',
                                     sub=_("%s bags", round(bags.get('rice', 0.0))), action=rice_action))
        if wanted('cover'):
            owed = self._cl_open_items(ctx, ['cl_rice_loan_account_id', 'cl_rice_service_account_id'])
            value = self._cl_stock_value(ctx, kg)
            cover = value / owed * 100.0 if owed > 0 else 0.0
            band = 'red' if cover < 120 else 'amber' if cover <= 200 else 'green'
            kpis.append(self._cl_kpi(
                'cover', _("Collateral cover"), round(cover, 1), fmt='pct', band=band if owed > 0 else None,
                sub=_("Stock worth %s", company.currency_id.format(value)),
                action=self._act('cl.rice.farmer.season', [('id', 'in', ctx['fs'].ids), ('low_cover', '=', True)]
                                 if 'low_cover' in self.env['cl.rice.farmer.season']._fields
                                 and self.env['cl.rice.farmer.season']._fields['low_cover'].store
                                 else [('id', 'in', ctx['fs'].ids)], _("Farmer Seasons"))))
        if wanted('active'):
            active = ctx['fs'].filtered(lambda fs: fs.state != 'draft')
            month_start = ctx['today'].replace(day=1)
            Partner = self.env['res.partner']
            new_domain = [('is_farmer', '=', True), ('create_date', '>=', month_start)]
            new = Partner.search_count(new_domain)
            prev_new = Partner.search_count([('is_farmer', '=', True),
                                             ('create_date', '>=', month_start - relativedelta(months=1)),
                                             ('create_date', '<', month_start)])
            reg_months = dict(Partner._read_group([('is_farmer', '=', True), ('create_date', '>=', months[0])],
                                                  ['create_date:month'], ['__count']))
            kpis.append(self._cl_kpi(
                'active', _("Active farmers"), len(active), fmt='count',
                sub=_("%s new this month", new), previous=None,
                spark=self._cl_monthly({k.date() if hasattr(k, 'date') else k: v for k, v in reg_months.items()}, months),
                action=self._act('cl.rice.farmer.season', [('id', 'in', active.ids)], _("Active Farmers"))))
            kpis[-1]['trend'] = round((new - prev_new) / prev_new * 100.0, 1) if prev_new else None
        if wanted('cash'):
            kpis.append(self._cl_cash_kpi(ctx))
        return {'kpis': kpis}

    def _cl_cash_kpi(self, ctx):
        _ = self.env._
        company = ctx['company']
        journals = self.env['account.journal'].sudo().search([
            ('company_id', '=', company.id), ('type', 'in', ('cash', 'bank')), ('default_account_id', '!=', False)])
        accounts = journals.default_account_id
        groups = self.env['account.move.line'].sudo()._read_group([
            ('company_id', '=', company.id), ('parent_state', '=', 'posted'),
            ('account_id', 'in', accounts.ids), ('date', '<=', ctx['today'])], ['account_id'], ['balance:sum'])
        by_account = dict(groups)
        detail = [{'name': j.name, 'value': by_account.get(j.default_account_id, 0.0)} for j in journals]
        total = sum(d['value'] for d in detail)
        months = self._cl_months(ctx)
        spark = []
        for month in months:
            end = month + relativedelta(months=1, days=-1)
            spark.append(self.env['account.move.line'].sudo()._read_group([
                ('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                ('account_id', 'in', accounts.ids), ('date', '<=', end)], [], ['balance:sum'])[0][0] or 0.0)
        kpi = self._cl_kpi(
            'cash', _("Cash position today"), total, spark=spark,
            sub=", ".join(f"{d['name']} {company.currency_id.format(d['value'])}" for d in detail if d['value']),
            action=self._act('cl.rice.report.cash.book', [('date', '<=', ctx['today'])], _("Cash Book")))
        kpi['detail'] = detail
        return kpi

    # ------------------------------------------------------------------
    # A. Income and profit
    # ------------------------------------------------------------------
    def _cl_section_income(self, ctx):
        _ = self.env._
        d_from, d_to = ctx['date_from'], ctx['date_to']
        company = ctx['company']
        income_accounts = self._cl_income_accounts(ctx)
        labels = {key: label for _f, key, label in INCOME_LINES}
        labels['milling_walkin'] = _("Milling (walk-in)")
        labels['milling'] = _("Milling (farmers)")
        groups = self._cl_sum_lines(ctx, income_accounts, d_from, d_to, -1.0, ['account_id', 'journal_id', 'date:month'])
        walkin = company.cl_rice_walkin_journal_id
        series = defaultdict(lambda: defaultdict(float))
        totals = defaultdict(float)
        months = sorted({month for (_a, _j, month) in groups})
        for (account, journal, month), amount in groups.items():
            key = income_accounts[account]
            if key == 'milling' and journal == walkin:
                key = 'milling_walkin'
            series[key][month] += amount
            totals[key] += amount
        income_chart = {
            'labels': [m.strftime('%b %Y') for m in months],
            'datasets': [{'key': key, 'label': labels[key], 'data': [round(series[key][m], 2) for m in months]}
                         for key in labels if key in series],
            'actions': {key: self._act('account.move.line', self._cl_line_domain(
                ctx, [a for a, k in income_accounts.items() if k == key.replace('_walkin', '')], d_from, d_to)
                & (Domain('journal_id', '=', walkin.id) if key == 'milling_walkin' else
                   Domain('journal_id', '!=', walkin.id) if key == 'milling' else Domain.TRUE),
                labels[key]) for key in series},
        }
        cost_accounts = self._cl_accounts(ctx, COST_LINES)
        cost_labels = {key: label for _f, key, label in COST_LINES}
        cost_groups = self._cl_sum_lines(ctx, cost_accounts, d_from, d_to, 1.0, ['account_id']) if not ctx['service_type'] else {}
        costs = defaultdict(float)
        for account, amount in cost_groups.items():
            costs[cost_accounts[account]] += amount
        cost_chart = {
            'labels': [cost_labels[key] for key in costs],
            'keys': list(costs),
            'data': [round(costs[key], 2) for key in costs],
            'actions': {key: self._act('account.move.line', self._cl_line_domain(
                ctx, [a for a, k in cost_accounts.items() if k == key], d_from, d_to), cost_labels[key]) for key in costs},
        }
        income_total = sum(totals.values())
        cost_total = sum(costs.values())
        waterfall = [{'label': labels[key], 'value': round(totals[key], 2), 'kind': 'income'} for key in labels if totals.get(key)]
        waterfall += [{'label': cost_labels[key], 'value': -round(costs[key], 2), 'kind': 'cost'} for key in costs]
        waterfall.append({'label': _("Net profit"), 'value': round(income_total - cost_total, 2), 'kind': 'total'})
        # profit per month, and the same months of the previous season when comparing
        profit_months = self._cl_profit_by_month(ctx, d_from, d_to)
        trend = {'labels': [m.strftime('%b %Y') for m in sorted(profit_months)],
                 'data': [round(profit_months[m], 2) for m in sorted(profit_months)], 'previous': []}
        if ctx['compare'] and ctx['previous_season'] and ctx['season']:
            shift = relativedelta(ctx['season'].date_start, ctx['previous_season'].date_start)
            prev = self._cl_profit_by_month(ctx, d_from - shift, d_to - shift)
            trend['previous'] = [round(prev.get(m - shift, 0.0), 2) for m in sorted(profit_months)]
            trend['previous_label'] = ctx['previous_season'].name
        return {
            'income_by_line': income_chart,
            'costs': cost_chart,
            'waterfall': waterfall,
            'profit_trend': trend,
            'totals': {'income': income_total, 'costs': cost_total, 'profit': income_total - cost_total},
        }

    def _cl_profit_by_month(self, ctx, d_from, d_to):
        income = self._cl_sum_lines(ctx, self._cl_income_accounts(ctx), d_from, d_to, -1.0, ['date:month'])
        costs = self._cl_sum_lines(ctx, self._cl_accounts(ctx, COST_LINES), d_from, d_to, 1.0, ['date:month']) \
            if not ctx['service_type'] else {}
        months = set(income) | set(costs)
        return {m: income.get(m, 0.0) - costs.get(m, 0.0) for m in months}

    # ------------------------------------------------------------------
    # B. Farmers and credit
    # ------------------------------------------------------------------
    def _cl_section_farmers(self, ctx):
        _ = self.env._
        fss = ctx['fs']
        receivables = ['cl_rice_loan_account_id', 'cl_rice_service_account_id']
        owed_by_fs = self._cl_open_items(ctx, receivables, groupby=['cl_farmer_season_id'])
        _kg, _bags, per_fs = self._cl_farmer_stock(ctx)
        top = sorted(((fs, amount) for fs, amount in owed_by_fs.items() if amount > 0), key=lambda x: -x[1])[:10]
        top_rows = []
        for fs, amount in top:
            value = self._cl_stock_value(ctx, per_fs.get(fs, {}), fs.season_id)
            top_rows.append({
                'id': fs.id, 'name': f"{fs.partner_id.cl_farmer_code or ''} {fs.partner_id.name}".strip(),
                'owed': round(amount, 2), 'cover': round(value / amount * 100.0, 1) if amount else 0.0,
                'action': self._act('cl.rice.farmer.season', [('id', '=', fs.id)], fs.partner_id.name, ('form',)),
            })
        # ageing of what farmers owe, by the date of each open item
        aged = self._cl_open_items(ctx, receivables, groupby=['date:day'])
        buckets = [(_("0-30 days"), 0, 30), (_("31-60 days"), 31, 60), (_("61-90 days"), 61, 90), (_("90+ days"), 91, 100000)]
        ageing = []
        for label, low, high in buckets:
            amount = sum(v for day, v in aged.items() if low <= (ctx['today'] - day).days <= high)
            ageing.append({'label': label, 'value': round(amount, 2), 'action': self._act('account.move.line', [
                ('cl_farmer_season_id', 'in', fss.ids), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                ('account_id', 'in', [ctx['company'][f].id for f in receivables if ctx['company'][f]]),
                ('date', '<=', ctx['today'] - timedelta(days=low)),
                ('date', '>=', ctx['today'] - timedelta(days=min(high, 36500)))], label)})
        # credit exposure against the limit (computed in batch for all farmer seasons)
        over, near = [], []
        open_fss = fss.filtered(lambda fs: fs.state not in ('draft', 'settled', 'closed'))
        for fs in open_fss:
            limit = fs.credit_limit
            if not limit:
                continue
            usage = fs.exposure / limit * 100.0
            row = {'id': fs.id, 'name': fs.partner_id.name, 'exposure': round(fs.exposure, 2),
                   'limit': round(limit, 2), 'usage': round(usage, 1)}
            if usage > 100:
                over.append(row)
            elif usage > 80:
                near.append(row)
        # loan recovery: what was lent, recovered from crop sales and from cash, still outstanding
        loan_domain = [('farmer_season_id', 'in', fss.ids), ('state', 'in', ('disbursed', 'settled')),
                       ('loan_type', '!=', 'carry_forward')]
        disbursed = self.env['cl.rice.loan']._read_group(loan_domain, [], ['amount:sum'])[0][0] or 0.0
        outstanding = max(self._cl_open_items(ctx, ['cl_rice_loan_account_id']), 0.0)
        recovered = max(disbursed - outstanding, 0.0)
        payments = dict(self.env['cl.rice.farmer.payment']._read_group(
            [('farmer_season_id', 'in', fss.ids), ('state', '=', 'posted')], ['payment_type'], ['amount:sum']))
        from_sales, from_cash = payments.get('buyer_payment', 0.0), payments.get('repayment', 0.0)
        share = from_sales / (from_sales + from_cash) if from_sales + from_cash else 0.0
        funnel = [
            {'label': _("Disbursed"), 'value': round(disbursed, 2),
             'action': self._act('cl.rice.loan', loan_domain, _("Loans"))},
            {'label': _("Recovered through sales"), 'value': round(recovered * share, 2),
             'action': self._act('cl.rice.farmer.payment', [('farmer_season_id', 'in', fss.ids),
                                                            ('payment_type', '=', 'buyer_payment')], _("Buyer Payments"))},
            {'label': _("Recovered in cash"), 'value': round(recovered * (1 - share), 2),
             'action': self._act('cl.rice.farmer.payment', [('farmer_season_id', 'in', fss.ids),
                                                            ('payment_type', '=', 'repayment')], _("Repayments"))},
            {'label': _("Outstanding"), 'value': round(outstanding, 2),
             'action': self._act('cl.rice.loan', loan_domain + [('state', '=', 'disbursed')], _("Open Loans"))},
        ]
        states = dict(self.env['cl.rice.farmer.season']._fields['state']._description_selection(self.env))
        by_state = dict(self.env['cl.rice.farmer.season']._read_group(
            [('id', 'in', fss.ids)], ['state'], ['__count']))
        pipeline = [{'key': key, 'label': label, 'value': by_state.get(key, 0),
                     'action': self._act('cl.rice.farmer.season', [('id', 'in', fss.ids), ('state', '=', key)], label)}
                    for key, label in states.items()]
        return {
            'top_owed': top_rows,
            'ageing': ageing,
            'over_limit': sorted(over, key=lambda r: -r['usage'])[:20],
            'near_limit': sorted(near, key=lambda r: -r['usage'])[:20],
            'over_count': len(over),
            'near_count': len(near),
            'funnel': funnel,
            'pipeline': pipeline,
        }

    # ------------------------------------------------------------------
    # C. Stock and warehouse
    # ------------------------------------------------------------------
    def _cl_section_stock(self, ctx):
        _ = self.env._
        company = ctx['company']
        partitions = [
            ('paddy', _("Paddy"), company.cl_rice_paddy_location_id),
            ('rice', _("Milled Rice"), company.cl_rice_rice_location_id),
            ('byproducts', _("By-products"), company.cl_rice_byproduct_location_id),
        ]
        groups = self._cl_store_quants(ctx, ['location_id', 'owner_id'])
        by_partition = defaultdict(lambda: {'farmer': 0.0, 'mill': 0.0})
        for location, owner, qty in groups:
            owner_type = 'farmer' if owner and owner != company.partner_id else 'mill'
            for key, _label, partition in partitions:
                if partition and location._child_of(partition):
                    by_partition[key][owner_type] += qty
        partition_chart = {
            'labels': [label for _k, label, _p in partitions],
            'farmer': [round(by_partition[key]['farmer'], 2) for key, _l, _p in partitions],
            'mill': [round(by_partition[key]['mill'], 2) for key, _l, _p in partitions],
            'actions': [self._act('stock.quant', [('location_id', 'child_of', p.id), ('quantity', '>', 0)], label)
                        for _k, label, p in partitions if p],
        }
        d_from, d_to = ctx['date_from'], ctx['date_to']
        intake_domain = [('state', '=', 'done'), ('company_id', '=', company.id), ('date', '>=', d_from),
                         ('date', '<=', fields.Datetime.to_string(d_to + timedelta(days=1)))]
        release_domain = [('state', '=', 'released'), ('company_id', '=', company.id),
                          ('released_date', '>=', d_from),
                          ('released_date', '<=', fields.Datetime.to_string(d_to + timedelta(days=1)))]
        if ctx['farmer_filter']:
            intake_domain.append(('farmer_season_id', 'in', ctx['fs'].ids))
            release_domain.append(('farmer_season_id', 'in', ctx['fs'].ids))
        intakes = dict(self.env['cl.rice.intake'].sudo()._read_group(intake_domain, ['date:week'], ['net_weight:sum']))
        releases = dict(self.env['cl.rice.stock.release'].sudo()._read_group(
            release_domain, ['released_date:week'], ['quantity:sum']))
        collections = dict(self.env['cl.rice.walkin.collection'].sudo()._read_group(
            [('state', '=', 'released'), ('company_id', '=', company.id), ('released_date', '>=', d_from)],
            ['released_date:week'], ['quantity:sum'])) if not ctx['farmer_filter'] else {}
        weeks = sorted({self._cl_day(w) for w in list(intakes) + list(releases) + list(collections)})
        intake_w = {self._cl_day(k): v for k, v in intakes.items()}
        release_w = defaultdict(float)
        for k, v in list(releases.items()) + list(collections.items()):
            release_w[self._cl_day(k)] += v
        trend = self._cl_stock_trend(ctx, weeks)
        flows = {
            'labels': [w.strftime('%d %b') for w in weeks],
            'intake': [round(intake_w.get(w, 0.0), 2) for w in weeks],
            'released': [round(release_w.get(w, 0.0), 2) for w in weeks],
            'stock': trend,
            'actions': {
                'intake': self._act('cl.rice.intake', intake_domain, _("Intakes")),
                'released': self._act('cl.rice.stock.release', release_domain, _("Releases")),
            },
        }
        # farmers with stock and nothing owed: ready to release
        _kg, _bags, per_fs = self._cl_farmer_stock(ctx)
        owed, _due = ctx['fs']._cl_open_balances() if ctx['fs'] else ({}, {})
        ready = [{'id': fs.id, 'name': fs.partner_id.name, 'kg': round(sum(stock.values()), 2),
                  'action': self._act('cl.rice.farmer.season', [('id', '=', fs.id)], fs.partner_id.name, ('form',))}
                 for fs, stock in per_fs.items() if owed.get(fs.id, 0.0) <= 0 and sum(stock.values()) > 0]
        ready.sort(key=lambda r: -r['kg'])
        month_start = ctx['today'].replace(day=1)
        fee_domain = [('company_id', '=', company.id), ('date', '>=', month_start)]
        if ctx['farmer_filter']:
            fee_domain.append(('farmer_season_id', 'in', ctx['fs'].ids))
        fees_month = self.env['cl.rice.storage.fee'].sudo()._read_group(fee_domain, [], ['amount:sum'])[0][0] or 0.0
        lot_groups = self._cl_store_quants(ctx, ['lot_id'], extra=[('owner_id', '!=', False),
                                                                   ('owner_id', '!=', company.partner_id.id)])
        lots = self.env['stock.lot'].concat(lot for lot, _qty in lot_groups)
        qty_by_lot = dict(lot_groups)
        oldest = []
        for lot in lots.filtered('cl_first_in_date').sorted('cl_first_in_date')[:10]:
            oldest.append({'lot': lot.name, 'owner': lot.cl_owner_id.name, 'kg': round(qty_by_lot[lot], 2),
                           'days': (ctx['today'] - lot.cl_first_in_date.date()).days,
                           'action': self._act('stock.lot', [('id', '=', lot.id)], lot.name, ('form',))})
        byproducts = []
        income_account = company.cl_rice_byproduct_income_account_id
        for role, label in (('bran', _("Bran")), ('husk', _("Husk"))):
            product = self.env.ref(f'cl_rice_mill.product_{role}', raise_if_not_found=False)
            if not product:
                continue
            rows = self._cl_store_quants(dict(ctx, farmer_filter=False), [], extra=[('product_id', '=', product.id)])
            kg = (rows[0][0] or 0.0) if rows else 0.0
            sales = self.env['account.move.line'].sudo()._read_group([
                ('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('product_id', '=', product.id),
                ('account_id', '=', income_account.id), ('date', '>=', d_from), ('date', '<=', d_to)],
                [], ['balance:sum', 'quantity:sum'])[0] if income_account else (0.0, 0.0)
            byproducts.append({
                'label': label, 'kg': round(kg, 2),
                'value': round(kg * product.with_company(company).standard_price, 2),
                'sales': round(-(sales[0] or 0.0), 2), 'sold_kg': round(sales[1] or 0.0, 2),
                'action': self._act('stock.quant', [('product_id', '=', product.id), ('quantity', '>', 0)], label),
            })
        return {
            'partitions': partition_chart,
            'flows': flows,
            'ready': ready[:15],
            'ready_count': len(ready),
            'storage_fees_month': fees_month,
            'oldest_lots': oldest,
            'byproducts': byproducts,
        }

    def _cl_day(self, value):
        return value.date() if hasattr(value, 'date') and callable(value.date) else value

    def _cl_stock_trend(self, ctx, weeks):
        """Kg in the stores at the end of each week (moves into minus moves out of the stores)."""
        if not weeks:
            return []
        locations = ctx['company']._cl_rice_store_locations()
        if not locations:
            return [0.0 for _w in weeks]
        query = SQL("""
            SELECT date_trunc('week', ml.date)::date AS week,
                   SUM(CASE WHEN dest.parent_path LIKE %(path)s AND src.parent_path NOT LIKE %(path)s THEN ml.quantity
                            WHEN src.parent_path LIKE %(path)s AND dest.parent_path NOT LIKE %(path)s THEN -ml.quantity
                            ELSE 0 END)
              FROM stock_move_line ml
              JOIN stock_location src ON src.id = ml.location_id
              JOIN stock_location dest ON dest.id = ml.location_dest_id
              JOIN product_product pp ON pp.id = ml.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE ml.state = 'done' AND ml.company_id = %(company)s AND pt.cl_rice_product IS NOT NULL
               AND ml.date < %(end)s
          GROUP BY 1
          ORDER BY 1
        """, path=f"{locations[0].parent_path}%", company=ctx['company'].id,
            end=weeks[-1] + timedelta(days=7))
        self.env['stock.move.line'].flush_model(['date', 'quantity', 'state', 'location_id', 'location_dest_id'])
        self.env.cr.execute(query)
        running = 0.0
        balance_by_week = {}
        for week, net in self.env.cr.fetchall():
            running += net or 0.0
            balance_by_week[week] = running
        result, last = [], 0.0
        before = [w for w in balance_by_week if w < weeks[0]]
        if before:
            last = balance_by_week[max(before)]
        for week in weeks:
            last = balance_by_week.get(week, last)
            result.append(round(last, 2))
        return result

    # ------------------------------------------------------------------
    # D. Milling
    # ------------------------------------------------------------------
    def _cl_section_milling(self, ctx):
        _ = self.env._
        company = ctx['company']
        d_from, d_to = ctx['date_from'], ctx['date_to']
        domain = [('state', '=', 'done'), ('company_id', '=', company.id), ('date_end', '>=', d_from),
                  ('date_end', '<', fields.Datetime.to_string(d_to + timedelta(days=1)))]
        if ctx['farmer_filter']:
            domain.append(('farmer_season_id', 'in', ctx['fs'].ids))
        if ctx['vehicle_id']:
            domain.append(('vehicle_id', '=', ctx['vehicle_id']))
        Job = self.env['cl.rice.milling.job'].sudo()
        weekly = Job._read_group(domain, ['vehicle_id', 'date_end:week'], ['input_kg:sum', 'rice_kg:sum'])
        weeks = sorted({self._cl_day(w) for _v, w, _i, _r in weekly})
        machines = sorted({v for v, _w, _i, _r in weekly}, key=lambda v: v.display_name or '')
        per_week = defaultdict(float)
        rice_week, input_week = defaultdict(float), defaultdict(float)
        for vehicle, week, kg, rice in weekly:
            per_week[vehicle, self._cl_day(week)] += kg
            rice_week[self._cl_day(week)] += rice
            input_week[self._cl_day(week)] += kg
        volume = {
            'labels': [w.strftime('%d %b') for w in weeks],
            'datasets': [{'label': v.display_name or _("No machine"),
                          'data': [round(per_week[v, w], 2) for w in weeks]} for v in machines],
        }
        recovery_trend = [round(rice_week[w] / input_week[w] * 100.0, 2) if input_week[w] else 0.0 for w in weeks]
        per_machine = Job._read_group(domain, ['vehicle_id'], ['input_kg:sum', 'rice_kg:sum', '__count'])
        recovery_machine = [{'label': v.display_name or _("No machine"), 'kg': round(i, 2),
                             'recovery': round(r / i * 100.0, 2) if i else 0.0, 'count': c,
                             'action': self._act('cl.rice.milling.job', domain + [('vehicle_id', '=', v.id)],
                                                 v.display_name or _("Milling"))}
                            for v, i, r, c in per_machine]
        # recovery per paddy grade, through the intake grade of the milled lot
        per_lot = Job._read_group(domain, ['input_lot_id'], ['input_kg:sum', 'rice_kg:sum'])
        grades = {lot: grade for lot, grade in self.env['cl.rice.intake'].sudo()._read_group(
            [('lot_id', 'in', [lot.id for lot, _i, _r in per_lot]), ('grade', '!=', False)], ['lot_id', 'grade'])}
        grade_labels = dict(self.env['cl.rice.intake']._fields['grade']._description_selection(self.env))
        by_grade = defaultdict(lambda: [0.0, 0.0])
        for lot, kg, rice in per_lot:
            grade = grades.get(lot, False)
            by_grade[grade][0] += kg
            by_grade[grade][1] += rice
        recovery_grade = [{'label': grade_labels.get(g, _("Not graded")), 'recovery': round(v[1] / v[0] * 100.0, 2)
                           if v[0] else 0.0, 'kg': round(v[0], 2)} for g, v in sorted(by_grade.items(), key=lambda x: str(x[0]))]
        modes = dict((mode, (kg, fee)) for mode, kg, fee in Job._read_group(domain, ['mode'], ['input_kg:sum', 'fee_amount:sum']))
        mode_split = [{'label': _("Farmers"), 'kg': round(modes.get('farmer', (0, 0))[0], 2),
                       'income': round(modes.get('farmer', (0, 0))[1], 2),
                       'action': self._act('cl.rice.milling.job', domain + [('mode', '=', 'farmer')], _("Farmer Milling"))},
                      {'label': _("Walk-in"), 'kg': round(modes.get('walkin', (0, 0))[0], 2),
                       'income': round(modes.get('walkin', (0, 0))[1], 2),
                       'action': self._act('cl.rice.milling.job', domain + [('mode', '=', 'walkin')], _("Walk-in Milling"))}]
        # utilisation: hours milled against the working hours of the period
        Job.flush_model(['date_start', 'date_end', 'vehicle_id', 'state'])
        self.env.cr.execute(SQL("""
            SELECT vehicle_id, SUM(EXTRACT(EPOCH FROM (date_end - date_start))) / 3600.0
              FROM cl_rice_milling_job
             WHERE state = 'done' AND company_id = %(company)s AND date_end >= %(start)s AND date_end < %(end)s
               AND date_start IS NOT NULL AND date_end > date_start
          GROUP BY vehicle_id
        """, company=company.id, start=d_from, end=d_to + timedelta(days=1)))
        hours = dict(self.env.cr.fetchall())
        days = sum(1 for n in range((d_to - d_from).days + 1) if (d_from + timedelta(days=n)).weekday() < 6)
        available = days * (company.cl_rice_mill_hours_per_day or 10.0)
        mills = self.env['fleet.vehicle'].sudo().search([('cl_machine_category', '=', 'mill'),
                                                          ('company_id', 'in', (company.id, False))])
        utilisation = [{'label': m.display_name, 'hours': round(hours.get(m.id, 0.0), 1), 'available': available,
                        'pct': round(hours.get(m.id, 0.0) / available * 100.0, 1) if available else 0.0} for m in mills]
        if hours.get(None):
            utilisation.append({'label': _("No machine"), 'hours': round(hours[None], 1), 'available': available,
                                'pct': round(hours[None] / available * 100.0, 1) if available else 0.0})
        return {
            'volume': volume,
            'recovery_trend': {'labels': volume['labels'], 'data': recovery_trend},
            'recovery_machine': recovery_machine,
            'recovery_grade': recovery_grade,
            'modes': mode_split,
            'utilisation': utilisation,
        }

    # ------------------------------------------------------------------
    # E. Machines and fleet
    # ------------------------------------------------------------------
    def _cl_section_machines(self, ctx):
        _ = self.env._
        company = ctx['company']
        d_from, d_to = ctx['date_from'], ctx['date_to']
        vehicles = self.env['fleet.vehicle'].sudo().search([('cl_machine_category', '!=', False),
                                                            ('company_id', 'in', (company.id, False))])
        if ctx['vehicle_id']:
            vehicles = vehicles.filtered(lambda v: v.id == ctx['vehicle_id'])
        machine_ctx = dict(ctx, vehicle_id=False)
        income_accounts = self._cl_income_accounts(machine_ctx)
        cost_fields = {'fuel': 'cl_rice_machine_cost_account_id', 'repairs': 'cl_rice_repair_account_id',
                       'wages': 'cl_rice_operator_wage_account_id', 'transport': 'cl_rice_transport_account_id'}
        ML = self.env['account.move.line'].sudo()
        base = self._cl_line_domain(machine_ctx, list(income_accounts) + [company[f] for f in cost_fields.values()
                                                                          if company[f]], d_from, d_to)
        groups = ML._read_group(base & Domain('vehicle_id', 'in', vehicles.ids), ['vehicle_id', 'account_id'], ['balance:sum'])
        figures = defaultdict(lambda: defaultdict(float))
        for vehicle, account, balance in groups:
            if account in income_accounts:
                figures[vehicle]['income'] -= balance
            else:
                for key, field_name in cost_fields.items():
                    if company[field_name] == account:
                        figures[vehicle][key] += balance
        job_domain = [('state', '=', 'charged'), ('date', '>=', d_from), ('date', '<=', d_to),
                      ('vehicle_id', 'in', vehicles.ids)]
        if ctx['farmer_filter']:
            job_domain.append(('farmer_season_id', 'in', ctx['fs'].ids))
        work = self.env['cl.rice.service.job'].sudo()._read_group(
            job_domain, ['vehicle_id', 'uom'], ['quantity:sum', 'fuel_litres:sum'])
        units = dict(self.env['cl.rice.service.job']._fields['uom']._description_selection(self.env))
        worked = defaultdict(lambda: defaultdict(float))
        litres_acre = {}
        for vehicle, uom, qty, litres in work:
            worked[vehicle][uom] += qty
            if uom == 'acre' and qty:
                litres_acre[vehicle] = litres / qty
        mill_hours = {}
        if vehicles.filtered(lambda v: v.cl_machine_category == 'mill'):
            self.env.cr.execute(SQL("""
                SELECT vehicle_id, SUM(EXTRACT(EPOCH FROM (date_end - date_start))) / 3600.0
                  FROM cl_rice_milling_job
                 WHERE state = 'done' AND vehicle_id = ANY(%(ids)s) AND date_end >= %(start)s AND date_end < %(end)s
                   AND date_end > date_start
              GROUP BY vehicle_id
            """, ids=vehicles.ids, start=d_from, end=d_to + timedelta(days=1)))
            mill_hours = dict(self.env.cr.fetchall())
        trips = dict(((v, km) for v, km in self.env['cl.rice.trip'].sudo()._read_group(
            [('state', '=', 'posted'), ('date', '>=', d_from), ('date', '<=', d_to), ('vehicle_id', 'in', vehicles.ids)],
            ['vehicle_id'], ['km:sum'])))
        average = sum(litres_acre.values()) / len(litres_acre) if litres_acre else 0.0
        rows = []
        for vehicle in vehicles:
            fig = figures.get(vehicle, {})
            costs = sum(fig.get(k, 0.0) for k in cost_fields)
            if vehicle.cl_machine_category == 'mill':
                qty, unit = mill_hours.get(vehicle.id, 0.0), _("hours")
            elif vehicle.cl_machine_category in ('motorcycle', 'car', 'truck'):
                qty, unit = trips.get(vehicle, 0.0), _("km")
            else:
                done = worked.get(vehicle, {})
                unit_key = max(done, key=done.get) if done else 'acre'
                qty, unit = done.get(unit_key, 0.0), units.get(unit_key, unit_key).lower()
            efficiency = litres_acre.get(vehicle)
            rows.append({
                'id': vehicle.id, 'name': vehicle.display_name, 'category': vehicle.cl_machine_category,
                'income': round(fig.get('income', 0.0), 2), 'fuel': round(fig.get('fuel', 0.0), 2),
                'repairs': round(fig.get('repairs', 0.0), 2), 'wages': round(fig.get('wages', 0.0), 2),
                'transport': round(fig.get('transport', 0.0), 2),
                'net': round(fig.get('income', 0.0) - costs, 2),
                'work': round(qty, 2), 'unit': unit,
                'cost_per_unit': round(costs / qty, 2) if qty else 0.0,
                'litres_per_acre': round(efficiency, 2) if efficiency else None,
                'fuel_alert': bool(efficiency and average and efficiency > average * 1.3),
                'action': self._act('account.move.line', base & Domain('vehicle_id', '=', vehicle.id),
                                    vehicle.display_name),
            })
        rows.sort(key=lambda r: -r['net'])
        services = self.env['fleet.vehicle.log.services'].sudo().search([
            ('vehicle_id', 'in', vehicles.ids), ('state', 'in', ('new', 'running')),
            ('date_from', '<=', ctx['today'] + timedelta(days=14))], order='date_from', limit=20)
        maintenance = [{'vehicle': s.vehicle_id.display_name, 'service': s.service_type_id.name,
                        'date': fields.Date.to_string(s.date_from), 'overdue': bool(s.date_from and s.date_from < ctx['today']),
                        'action': self._act('fleet.vehicle.log.services', [('id', '=', s.id)], s.service_type_id.name,
                                            ('form',))} for s in services]
        return {'machines': rows, 'average_litres_per_acre': round(average, 2), 'maintenance': maintenance}

    # ------------------------------------------------------------------
    # F. Field operations
    # ------------------------------------------------------------------
    def _cl_section_field(self, ctx):
        _ = self.env._
        d_from, d_to = ctx['date_from'], ctx['date_to']
        Job = self.env['cl.rice.service.job']
        domain = [('farmer_season_id', 'in', ctx['fs'].ids), ('date', '>=', d_from), ('date', '<=', d_to),
                  ('state', 'in', ('done', 'charged'))]
        if ctx['service_type']:
            domain.append(('service_type', '=', ctx['service_type']))
        if ctx['vehicle_id']:
            domain.append(('vehicle_id', '=', ctx['vehicle_id']))
        per_officer = [{'name': officer.name or _("No officer"), 'count': count, 'quantity': round(qty, 2),
                        'income': round(amount, 2),
                        'action': self._act('cl.rice.service.job', domain + [('field_officer_id', '=', officer.id)],
                                            officer.name or _("Service Jobs"))}
                       for officer, count, qty, amount in Job._read_group(
                           domain, ['field_officer_id'], ['__count', 'quantity:sum', 'amount:sum'])]
        stale = fields.Datetime.now() - timedelta(days=2)
        draft_domain = [('farmer_season_id', 'in', ctx['fs'].ids), ('state', '=', 'draft'), ('source', '=', 'field')]
        Trip = self.env['cl.rice.trip']
        trip_draft = [('state', '=', 'draft'), ('source', '=', 'field')]
        if ctx['role'] == 'officer':
            trip_draft.append(('user_id', '=', self.env.uid))
        pending_farmers = [('is_farmer', '=', True), ('cl_farmer_state', '=', 'pending')]
        sync = [
            {'label': _("Service jobs from the field app to confirm"), 'value': Job.search_count(draft_domain),
             'old': Job.search_count(draft_domain + [('create_date', '<', stale)]),
             'action': self._act('cl.rice.service.job', draft_domain, _("Field Jobs to Confirm"))},
            {'label': _("Trips from the field app to post"), 'value': Trip.search_count(trip_draft),
             'old': Trip.search_count(trip_draft + [('create_date', '<', stale)]),
             'action': self._act('cl.rice.trip', trip_draft, _("Trips to Post"))},
            {'label': _("Farmers registered, waiting for approval"),
             'value': self.env['res.partner'].search_count(pending_farmers),
             'old': self.env['res.partner'].search_count(pending_farmers + [('create_date', '<', stale)]),
             'action': self._act('res.partner', pending_farmers, _("Farmers to Approve"))},
        ]
        trip_domain = [('state', '=', 'posted'), ('date', '>=', d_from), ('date', '<=', d_to)]
        if ctx['role'] == 'officer':
            trip_domain.append(('user_id', '=', self.env.uid))
        trips_officer = [{'name': user.name or _("Unknown"), 'km': round(km, 1), 'cost': round(cost, 2), 'count': count}
                         for user, km, cost, count in Trip._read_group(
                             trip_domain, ['user_id'], ['km:sum', 'fuel_cost:sum', '__count'])]
        trips_vehicle = [{'name': vehicle.display_name, 'km': round(km, 1), 'cost': round(cost, 2), 'count': count}
                         for vehicle, km, cost, count in Trip._read_group(
                             trip_domain, ['vehicle_id'], ['km:sum', 'fuel_cost:sum', '__count'])]
        return {'per_officer': per_officer, 'sync': sync, 'trips_officer': trips_officer,
                'trips_vehicle': trips_vehicle}

    # ------------------------------------------------------------------
    # G. Alerts
    # ------------------------------------------------------------------
    def _cl_section_alerts(self, ctx):
        _ = self.env._
        company = ctx['company']
        alerts = []
        fss = ctx['fs']
        FarmerSeason = self.env['cl.rice.farmer.season']
        overrides = FarmerSeason.search([('id', 'in', fss.ids), ('override_outstanding', '=', True)])
        late = overrides.filtered(lambda fs: fs.override_promised_date and fs.override_promised_date < ctx['today'])
        if overrides:
            alerts.append({'level': 'red' if late else 'amber', 'count': len(overrides),
                           'title': _("Released under manager override, payment still outstanding"),
                           'detail': _("%s past the promised date", len(late)) if late else '',
                           'action': self._act('cl.rice.farmer.season', [('id', 'in', overrides.ids)], _("Override Outstanding"))})
        open_fss = fss.filtered(lambda fs: fs.state not in ('draft', 'settled', 'closed'))
        over = open_fss.filtered('over_limit')
        if over:
            alerts.append({'level': 'red', 'count': len(over), 'title': _("Farmers over their credit limit"),
                           'action': self._act('cl.rice.farmer.season', [('id', 'in', over.ids)], _("Over Credit Limit"))})
        owed, _due = open_fss._cl_open_balances() if open_fss else ({}, {})
        _kg, _bags, per_fs = self._cl_farmer_stock(ctx)
        low_cover = []
        for fs in open_fss:
            balance = owed.get(fs.id, 0.0)
            if balance > 0:
                value = self._cl_stock_value(ctx, per_fs.get(fs, {}), fs.season_id)
                if value / balance * 100.0 < 120:
                    low_cover.append(fs.id)
        if low_cover:
            alerts.append({'level': 'red', 'count': len(low_cover), 'title': _("Collateral cover below 120%"),
                           'action': self._act('cl.rice.farmer.season', [('id', 'in', low_cover)], _("Low Collateral Cover"))})
        # stock held, money owed and nothing happening for 30 days
        holding = [fs for fs in per_fs if owed.get(fs.id, 0.0) > 0 and fs in open_fss]
        if holding:
            last = dict(self.env['account.move.line'].sudo()._read_group(
                [('cl_farmer_season_id', 'in', [fs.id for fs in holding]), ('parent_state', '=', 'posted')],
                ['cl_farmer_season_id'], ['date:max']))
            quiet = [fs.id for fs in holding if not last.get(fs) or (ctx['today'] - last[fs]).days > 30]
            if quiet:
                alerts.append({'level': 'amber', 'count': len(quiet),
                               'title': _("Holding stock with a balance owed, no activity for 30 days"),
                               'action': self._act('cl.rice.farmer.season', [('id', 'in', quiet)], _("Quiet Farmers"))})
        stale = fields.Datetime.now() - timedelta(days=2)
        drafts = self.env['cl.rice.service.job'].search([('farmer_season_id', 'in', fss.ids), ('state', '=', 'draft'),
                                                         ('source', '=', 'field'), ('create_date', '<', stale)])
        if drafts:
            alerts.append({'level': 'amber', 'count': len(drafts), 'title': _("Field app records waiting over 2 days"),
                           'action': self._act('cl.rice.service.job', [('id', 'in', drafts.ids)], _("Field Records"))})
        if ctx['role'] in ('manager', 'accountant'):
            unposted = self.env['hr.expense'].sudo().search([('company_id', '=', company.id),
                                                             ('account_move_id.state', '=', 'draft')])
            if unposted:
                alerts.append({'level': 'amber', 'count': len(unposted), 'title': _("Expenses with an unposted entry"),
                               'action': self._act('hr.expense', [('id', 'in', unposted.ids)], _("Unposted Expenses"))})
        age = company.cl_rice_dashboard_lot_age_days or 90
        lot_groups = self._cl_store_quants(ctx, ['lot_id'])
        old_lots = [lot.id for lot, _qty in lot_groups
                    if lot.cl_first_in_date and (ctx['today'] - lot.cl_first_in_date.date()).days > age]
        if old_lots:
            alerts.append({'level': 'amber', 'count': len(old_lots),
                           'title': _("Lots in store for more than %s days", age),
                           'action': self._act('stock.lot', [('id', 'in', old_lots)], _("Old Lots"))})
        if ctx['role'] == 'manager':
            machines = self._cl_section_machines(ctx)['machines']
            thirsty = [m for m in machines if m['fuel_alert']]
            if thirsty:
                alerts.append({'level': 'amber', 'count': len(thirsty),
                               'title': _("Machines using far more fuel per acre than average"),
                               'detail': ", ".join(m['name'] for m in thirsty),
                               'action': self._act('fleet.vehicle', [('id', 'in', [m['id'] for m in thirsty])],
                                                   _("Machines"))})
        alerts.sort(key=lambda a: (a['level'] != 'red', -a['count']))
        return {'alerts': alerts}

    # ------------------------------------------------------------------
    # Export, farmer season panel
    # ------------------------------------------------------------------
    @api.model
    def export_dashboard_xlsx(self, title, columns, rows):
        """A dashboard table as an XLSX file (base64)."""
        self._cl_role()
        import xlsxwriter  # noqa: PLC0415 (only needed for exports)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet((title or "Dashboard")[:31])
        bold = workbook.add_format({'bold': True, 'bg_color': '#E8F5E9', 'border': 1})
        number = workbook.add_format({'num_format': '#,##0.00'})
        for col, header in enumerate(columns):
            sheet.write(0, col, header, bold)
            sheet.set_column(col, col, max(len(str(header)) + 4, 14))
        for row_index, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                if isinstance(value, (int, float)):
                    sheet.write_number(row_index, col, value, number)
                else:
                    sheet.write(row_index, col, '' if value is None else str(value))
        workbook.close()
        return base64.b64encode(output.getvalue()).decode()

    @api.model
    def get_farmer_season_panel(self, fs_id):
        fs = self.env['cl.rice.farmer.season'].browse(fs_id)
        fs.check_access('read')
        fs = fs.sudo()
        statement = fs._cl_statement()
        charges = defaultdict(float)
        categories = dict(self.env['cl.rice.settlement.line']._fields['category']._description_selection(self.env))
        for line in statement['lines']:
            if line['kind'] == 'owed':
                charges[categories.get(line['category'], line['category'])] += line['amount']
        held = fs._cl_portal_stock()
        kg = defaultdict(float)
        for line in held:
            kg['paddy' if 'addy' in line['product'] else 'rice'] += line['kg']
        return {
            'currency': {'symbol': fs.currency_id.symbol, 'position': fs.currency_id.position},
            'balance': fs.balance_owed,
            'funds': fs.funds_held,
            'limit': fs.credit_limit,
            'exposure': fs.exposure,
            'cover': round(fs.collateral_cover, 1),
            'charges': [{'label': k, 'value': round(v, 2)} for k, v in charges.items()],
            'stock': held,
            'stock_kg': round(sum(line['kg'] for line in held), 2),
            'timeline': [{'date': fields.Date.to_string(e['date']) if e['date'] else '', 'title': e['title'],
                          'icon': e['icon'], 'amount': e['amount']} for e in fs._cl_portal_timeline()[:8]],
        }

    # ------------------------------------------------------------------
    # Analysis views (pivot and graph, to slice beyond the dashboard)
    # ------------------------------------------------------------------
    @api.model
    def action_open_analysis(self, kind):
        role = self._cl_role()
        if role not in ('manager', 'accountant') and kind not in ('stock', 'milling'):
            raise AccessError(self.env._("This analysis is for the office."))
        company = self.env.company
        ctx = {'company': company, 'farmer_filter': False, 'vehicle_id': False, 'service_type': False}
        income = list(self._cl_accounts(ctx, INCOME_LINES))
        costs = list(self._cl_accounts(ctx, COST_LINES))
        receivables = [company[f] for f in ('cl_rice_loan_account_id', 'cl_rice_service_account_id') if company[f]]
        views = [[False, 'pivot'], [False, 'graph'], [False, 'list']]
        _ = self.env._
        specs = {
            'income': (_("Income by Service Line"), 'account.move.line', self._cl_line_domain(ctx, income),
                       ['account_id'], ['date:month'], ['balance']),
            'machines': (_("Profit per Machine"), 'account.move.line',
                         self._cl_line_domain(ctx, income + costs) & Domain('vehicle_id', '!=', False),
                         ['vehicle_id'], ['account_id'], ['balance']),
            'ageing': (_("Farmer Ageing"), 'account.move.line', Domain([
                ('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                ('account_id', 'in', [a.id for a in receivables]), ('cl_farmer_season_id', '!=', False)]),
                       ['cl_farmer_season_id'], ['date:month'], ['amount_residual']),
            'stock': (_("Stock per Farmer and Lot"), 'stock.quant', Domain([
                ('location_id', 'child_of', company._cl_rice_store_locations().ids),
                ('product_id.cl_rice_product', '!=', False), ('quantity', '>', 0)]),
                      ['owner_id', 'lot_id'], ['product_id'], ['quantity']),
            'milling': (_("Milling Yield"), 'cl.rice.milling.job', Domain([
                ('state', '=', 'done'), ('company_id', '=', company.id)]),
                        ['vehicle_id'], ['date_end:month'], ['input_kg', 'rice_kg', 'recovery_pct']),
            'pnl': (_("Season Profit and Loss"), 'account.move.line', self._cl_line_domain(ctx, income + costs),
                    ['account_id'], ['date:month'], ['balance']),
        }
        name, model, domain, rows, cols, measures = specs[kind]
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'views': views,
            'domain': list(domain),
            'context': {
                'pivot_row_groupby': rows,
                'pivot_column_groupby': cols,
                'pivot_measures': measures,
                'graph_groupbys': rows,
                'graph_measure': measures[0],
                'search_default_posted': 0,
            },
            'help': _("Income shows as a negative balance in accounting: a negative total is a profit."
                      ) if model == 'account.move.line' else False,
        }

    # ------------------------------------------------------------------
    # Demo data (performance checks with a realistic volume)
    # ------------------------------------------------------------------
    @api.model
    def _cl_generate_demo(self, farmers=300, season=None):
        """A season with ``farmers`` farmers: loans, services, intakes, milling and payments."""
        if not self.env.user.has_group('cl_rice_mill.cl_rice_mill_group_manager'):
            raise AccessError(self.env._("Only a Rice Mill Manager can generate demo data."))
        company = self.env.company
        if not company.cl_rice_warehouse_id:
            raise UserError(self.env._("Run the Rice Mill setup first."))
        Season = self.env['cl.rice.season']
        today = fields.Date.context_today(self)
        season = season or Season.create({
            'name': f"Demo Season {today.year}", 'code': f"D{today.strftime('%y%m%d')}",
            'date_start': today - timedelta(days=200), 'date_end': today + timedelta(days=165),
            'state': 'active', 'reference_price': 900, 'rice_reference_price': 2200})
        Rate = self.env['cl.rice.service.rate']
        for service, uom, price in (('ploughing', 'acre', 50_000), ('harvesting', 'acre', 60_000), ('milling', 'kg', 50)):
            if not Rate._cl_get_rate(service, season.date_start, company):
                Rate.create({'service_type': service, 'uom': uom, 'price_farmer': price, 'price_walkin': price * 1.2,
                             'date_from': season.date_start, 'company_id': company.id})
        villages = ["Igurusi", "Ubaruku", "Chimala", "Mbuyuni", "Madibira", "Rujewa"]
        partners = self.env['res.partner'].create([{
            'name': f"Demo Farmer {index:03d}", 'is_farmer': True, 'cl_farmer_state': 'approved',
            'phone': f"25575{index:07d}", 'cl_village': villages[index % len(villages)], 'company_id': company.id,
        } for index in range(farmers)])
        fss = self.env['cl.rice.farmer.season'].create([{
            'partner_id': partner.id, 'season_id': season.id, 'company_id': company.id,
            'acreage': 2 + index % 5} for index, partner in enumerate(partners)])
        fss.action_activate()
        cash = self.env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'cash')], limit=1)
        loans = self.env['cl.rice.loan'].create([{
            'farmer_season_id': fs.id, 'amount': 100_000 + (index % 7) * 50_000, 'journal_id': cash.id,
            'date': season.date_start + timedelta(days=index % 60)} for index, fs in enumerate(fss)])
        loans.action_submit()
        loans._cl_approve(reason=self.env._("Demo data"))
        loans.action_disburse()
        jobs = self.env['cl.rice.service.job'].create([{
            'farmer_season_id': fs.id, 'service_type': 'ploughing', 'quantity': fs.acreage,
            'date': season.date_start + timedelta(days=20 + index % 40), 'fuel_litres': fs.acreage * 8,
        } for index, fs in enumerate(fss)])
        jobs.action_done()
        jobs.action_charge()
        intakes = self.env['cl.rice.intake'].create([{
            'farmer_season_id': fs.id, 'bags': int(fs.acreage * 12), 'gross_weight': fs.acreage * 12 * 90.5,
            'moisture': 13.5, 'grade': 'abc'[index % 3], 'date': fields.Datetime.now() - timedelta(days=60 - index % 50),
        } for index, fs in enumerate(fss)])
        intakes.action_confirm()
        for index, fs in enumerate(fss[: farmers // 2]):
            lot = fs._cl_find_lot(self.env.ref('cl_rice_mill.product_paddy'))
            job = self.env['cl.rice.milling.job'].create({
                'farmer_season_id': fs.id, 'input_lot_id': lot.id, 'input_kg': 500, 'rice_kg': 320,
                'broken_kg': 20, 'bran_kg': 60, 'husk_kg': 90,
                'date_start': fields.Datetime.now() - timedelta(days=30 - index % 25, hours=3),
                'date_end': fields.Datetime.now() - timedelta(days=30 - index % 25)})
            job.action_done()
        payments = self.env['cl.rice.farmer.payment'].create([{
            'farmer_season_id': fs.id, 'payment_type': 'repayment', 'amount': 50_000, 'journal_id': cash.id,
        } for fs in fss[::3]])
        payments.action_post()
        self._cl_clear_cache()
        return season
