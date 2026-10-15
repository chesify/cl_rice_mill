from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class ClRiceCommon(AccountTestInvoicingCommon):

    @classmethod
    def get_default_groups(cls):
        return super().get_default_groups() | cls.env.ref('cl_rice_mill.cl_rice_mill_group_manager')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._cl_rice_setup_company()
        cls.company.write({
            'cl_rice_ltv_percent': 50.0,
            'cl_rice_default_yield': 1000.0,
        })

        def user(login, *groups):
            return new_test_user(
                cls.env, login=login, name=login.replace('_', ' ').title(),
                group_ids=[cls.env.ref(g).id for g in groups],
                company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)],
            )

        cls.user_officer = user('rm_officer', 'cl_rice_mill.cl_rice_mill_group_field_officer')
        cls.user_officer_2 = user('rm_officer_2', 'cl_rice_mill.cl_rice_mill_group_field_officer')
        cls.user_warehouse = user('rm_warehouse', 'cl_rice_mill.cl_rice_mill_group_warehouse')
        cls.user_miller = user('rm_miller', 'cl_rice_mill.cl_rice_mill_group_mill_operator')
        cls.user_accountant = user('rm_accountant', 'cl_rice_mill.cl_rice_mill_group_accountant')
        cls.user_manager = user('rm_manager', 'cl_rice_mill.cl_rice_mill_group_manager')

        cls.season = cls.env['cl.rice.season'].create({
            'name': "Msimu 2026/27",
            'code': '2627',
            'date_start': '2026-07-01',
            'date_end': '2027-06-30',
            'reference_price': 1000.0,
            'rice_reference_price': 2000.0,
        })
        cls.season.action_activate()
        cls.farmer = cls.env['res.partner'].create({
            'name': "Juma Mwakalinga",
            'is_farmer': True,
            'cl_village': "Igurusi",
            'cl_field_officer_id': cls.user_officer.id,
        })
        cls.farmer_2 = cls.env['res.partner'].create({
            'name': "Neema Kibona",
            'is_farmer': True,
            'cl_village': "Ubaruku",
            'cl_field_officer_id': cls.user_officer_2.id,
        })
        cls.fs = cls.env['cl.rice.farmer.season'].create({
            'partner_id': cls.farmer.id,
            'season_id': cls.season.id,
            'acreage': 4.0,
        })
        cls.fs_2 = cls.env['cl.rice.farmer.season'].create({
            'partner_id': cls.farmer_2.id,
            'season_id': cls.season.id,
            'acreage': 2.0,
        })
        (cls.fs | cls.fs_2).action_activate()

        # cash journal whose payments are booked at once on an outstanding account
        cls.cash_journal = cls.company_data['default_journal_cash']
        cls.outstanding_account = cls.env['account.account'].create({
            'code': 'RMTEST01',
            'name': "Outstanding Payments (test)",
            'account_type': 'asset_current',
            'reconcile': True,
        })
        cls.cash_journal.outbound_payment_method_line_ids.payment_account_id = cls.outstanding_account
        cls.cash_journal.inbound_payment_method_line_ids.payment_account_id = cls.outstanding_account

        brand = cls.env['fleet.vehicle.model.brand'].create({'name': "Massey Ferguson"})
        model = cls.env['fleet.vehicle.model'].create({'name': "MF 375", 'brand_id': brand.id, 'vehicle_type': 'car'})
        cls.tractor = cls.env['fleet.vehicle'].create({
            'model_id': model.id,
            'license_plate': 'T 123 ABC',
            'cl_machine_category': 'tractor',
        })
        cls.operator = cls.env['hr.employee'].sudo().create({'name': "Baraka Operator"})
        cls.rate_ploughing = cls.env['cl.rice.service.rate'].create({
            'service_type': 'ploughing',
            'uom': 'acre',
            'price_farmer': 50_000,
            'price_walkin': 60_000,
            'date_from': '2026-01-01',
        })

    @classmethod
    def _create_loan(cls, fs, amount, **vals):
        return cls.env['cl.rice.loan'].create({
            'farmer_season_id': fs.id,
            'amount': amount,
            'journal_id': cls.cash_journal.id,
            **vals,
        })

    @classmethod
    def _disburse(cls, loan):
        loan.action_submit()
        loan.with_user(cls.user_manager).action_approve()
        loan.with_user(cls.user_accountant).action_disburse()
        return loan
