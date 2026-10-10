import unittest
from datetime import date
from app.accounting_engine import to_minor, from_minor, simple_interest_minor, allocate_kist_payment, profit_components, profit_minor, monthly_overdue_penalty_minor, assert_idempotent_match, allocate_minor

class AccountingEngineTests(unittest.TestCase):
    def test_kist_partial_payment_and_bulk_cap_are_exact(self):
        partial = allocate_kist_payment(50000, 20000, 15000)
        self.assertEqual(partial, {'posted_minor':15000,'cumulative_minor':35000,'remaining_minor':15000,'status':'partial'})
        capped = allocate_kist_payment(50000, 45000, 10000, cap_to_remaining=True)
        self.assertEqual(capped, {'posted_minor':5000,'cumulative_minor':50000,'remaining_minor':0,'status':'paid'})
        with self.assertRaises(ValueError):
            allocate_kist_payment(50000, 45000, 10000)

    def test_simple_interest_uses_integer_paise(self):
        self.assertEqual(simple_interest_minor(10_000_000, '2', 1), 200_000)
        self.assertEqual(simple_interest_minor(10_000_000, '2.5', 3), 750_000)
        self.assertEqual(simple_interest_minor(100, '2', 1), 2)
        with self.assertRaises(ValueError):
            simple_interest_minor(-100, '2', 1)

    def test_paise_rounding_is_explicit(self):
        self.assertEqual(to_minor('1.005'), 101)
        self.assertEqual(from_minor(101), from_minor(101))
        with self.assertRaises(ValueError): to_minor('NaN')

    def test_profit_excludes_principal_and_counts_tagged_interest_once(self):
        rows = [
            {'type':'loan_repayment','amount':'1100','principal_repaid':'1000','interest':'100','loan_interest_collected':'100'},
            {'type':'interest','account':'bank','amount':'20'},
            {'type':'penalty','payment_category':'loan','amount':'5','loan_penalty_collected':'5'},
            {'type':'contribution','amount':'500'},
            {'type':'loan_disbursement','amount':'-1000'},
        ]
        self.assertEqual(profit_minor(rows, [{'amount':'10'}]), 11500)

    def test_reversal_offsets_original_interest_profit(self):
        original = {'type':'interest','account':'bank','amount':'25','amount_minor':2500}
        reversal = {'type':'reversal','original_type':'interest','reversal_of':'tx-1',
                    'account':'bank','amount':'-25','amount_minor':-2500,'interest':-25}
        self.assertEqual(profit_components(original)['income_total'], 2500)
        self.assertEqual(profit_components(reversal)['income_total'], -2500)
        self.assertEqual(profit_minor([original, reversal], []), 0)

    def test_reversal_offsets_minor_component_fields(self):
        original = {'type':'loan_repayment','amount':110,'amount_minor':11000,
                    'loan_interest_collected':10,'loan_interest_minor':1000,
                    'loan_penalty_collected':2,'loan_penalty_minor':200}
        reversal = {'type':'reversal','original_type':'loan_repayment','reversal_of':'tx-3',
                    'amount':-112,'amount_minor':-11200,'loan_interest_collected':-10,
                    'loan_interest_minor':-1000,'loan_penalty_collected':-2,'loan_penalty_minor':-200}
        self.assertEqual(profit_components(original)['income_total'], 1200)
        self.assertEqual(profit_components(reversal)['income_total'], -1200)
        self.assertEqual(profit_minor([original, reversal], []), 0)

    def test_generic_income_is_counted_and_reversal_offsets_it(self):
        original = {'type':'misc_income','amount':'75','amount_minor':7500}
        reversal = {'type':'reversal','original_type':'misc_income','reversal_of':'tx-2',
                    'amount':'-75','amount_minor':-7500}
        self.assertEqual(profit_components(original)['other_income'], 7500)
        self.assertEqual(profit_components(reversal)['other_income'], -7500)
        self.assertEqual(profit_minor([original, reversal], []), 0)

    def test_penalty_category_overrides_legacy_misfiled_component(self):
        loan_penalty = profit_components({'type':'penalty','amount':'10','amount_minor':1000,
                                          'penalty_category':'loan','other_penalty':'10'})
        self.assertEqual(loan_penalty['loan_penalties'], 1000)
        self.assertEqual(loan_penalty['other_penalties'], 0)
        bc_penalty = profit_components({'type':'penalty','amount':'10','amount_minor':1000,
                                        'penalty_category':'bc','other_penalty':'10'})
        self.assertEqual(bc_penalty['bc_penalties'], 1000)
        self.assertEqual(bc_penalty['other_penalties'], 0)

    def test_unknown_positive_transaction_is_not_silently_profit(self):
        self.assertEqual(profit_components({'type':'unrecognized_legacy_type','amount':'500'})['income_total'], 0)
        self.assertEqual(profit_components({'type':'misc_income','amount':'5'})['other_income'], 500)

    def test_legacy_penalty_categories_and_no_double_count(self):
        self.assertEqual(profit_components({'type':'penalty','amount':'3','payment_category':'other'})['other_penalties'], 300)
        self.assertEqual(profit_components({'type':'penalty','amount':'3','loan_penalty_collected':'3','payment_category':'loan'})['income_total'], 300)

    def test_bank_interest_is_not_double_counted_from_legacy_other_interest_field(self):
        row = {'type':'interest','account':'bank','amount':'50','amount_minor':5000,
               'other_interest':'50','other_interest_minor':5000}
        parts = profit_components(row)
        self.assertEqual(parts['bank_interest'], 5000)
        self.assertEqual(parts['other_interest'], 0)
        self.assertEqual(parts['income_total'], 5000)

    def test_penalty_accrues_per_missed_cycle_and_not_origination_month(self):
        self.assertEqual(monthly_overdue_penalty_minor(date(2026,1,15), date(2026,2,10), 10, 100), 0)
        self.assertEqual(monthly_overdue_penalty_minor(date(2026,1,1), date(2026,3,12), 10, 100), (30+2)*100)

    def test_minor_component_fields_take_precedence_over_float_legacy_fields(self):
        row = {'type':'loan_repayment','amount':110,'amount_minor':11000,
               'loan_interest_collected':1.23,'loan_interest_minor':123,
               'principal_repaid':108.77}
        self.assertEqual(profit_components(row)['loan_interest'], 123)

    def test_share_profit_allocation_rounds_half_up_in_paise(self):
        self.assertEqual(allocate_minor(100, 1, 3), 33)
        self.assertEqual(allocate_minor(-100, 1, 3), -33)
        self.assertEqual(allocate_minor(100, 0, 3), 0)

    def test_idempotency_rejects_changed_amount_and_accepts_exact_retry(self):
        row = {'tenant_id':'tenant-a','member_id':'member-a','type':'contribution',
               'amount':100,'amount_minor':10000,'account':'cash','payment_category':'manual_contribution'}
        assert_idempotent_match(row, tenant_id='tenant-a', member_id='member-a', typ='contribution',
                                amount_minor=10000, account='cash', extra={'payment_category':'manual_contribution'})
        with self.assertRaises(ValueError):
            assert_idempotent_match(row, tenant_id='tenant-a', member_id='member-a', typ='contribution',
                                    amount_minor=10001, account='cash', extra={})

    def test_idempotency_rejects_changed_penalty_category(self):
        row = {'tenant_id':'tenant-a','member_id':'member-a','type':'penalty',
               'amount':10,'amount_minor':1000,'account':'cash','payment_category':'other','penalty_category':'bc'}
        with self.assertRaises(ValueError):
            assert_idempotent_match(row, tenant_id='tenant-a', member_id='member-a', typ='penalty',
                                    amount_minor=1000, account='cash', extra={'penalty_category':'loan'})

    def test_idempotency_preserves_original_requested_amount_when_bulk_payment_is_capped(self):
        row = {'tenant_id':'tenant-a','member_id':'member-a','type':'contribution',
               'amount':200,'amount_minor':20000,'idempotency_amount_minor':50000,'account':'cash'}
        assert_idempotent_match(row, tenant_id='tenant-a', member_id='member-a', typ='contribution',
                                amount_minor=50000, account='cash', extra={})
        with self.assertRaises(ValueError):
            assert_idempotent_match(row, tenant_id='tenant-a', member_id='member-a', typ='contribution',
                                    amount_minor=10000, account='cash', extra={})

if __name__ == '__main__': unittest.main()
