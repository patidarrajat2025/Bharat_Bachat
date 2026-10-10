import unittest
from app.journal import build_journal, build_expense_journal, reverse_journal

class JournalTests(unittest.TestCase):
    def test_loan_repayment_journal_balances_and_splits_income(self):
        j = build_journal({'type':'loan_repayment','amount':'110','amount_minor':11000,'account':'cash','principal_repaid':'100','loan_interest_collected':'8','loan_penalty_collected':'2'})
        self.assertTrue(j['balanced'])
        self.assertEqual(j['debit_minor'], 11000)
        self.assertEqual(j['credit_minor'], 11000)
        self.assertEqual({x['account_code'] for x in j['lines']}, {'cash','loan_receivable','loan_interest_income','loan_penalty_income'})

    def test_expense_journal_balances(self):
        j = build_expense_journal({'amount':'25.50','amount_minor':2550,'account':'cash','category':'office supplies'})
        self.assertEqual(j['debit_minor'], 2550)
        self.assertEqual(j['credit_minor'], 2550)
        self.assertEqual(j['lines'][0]['account_code'], 'expense:office_supplies')

    def test_reversal_swaps_debits_and_credits(self):
        original = build_journal({'type':'interest','amount':'25','amount_minor':2500,'account':'bank'})
        reversal = reverse_journal(original)
        self.assertEqual(reversal['debit_minor'], original['credit_minor'])
        self.assertEqual(reversal['credit_minor'], original['debit_minor'])
        self.assertEqual(reversal['lines'][0]['account_code'], 'bank')
        self.assertEqual(reversal['lines'][0]['credit_minor'], 2500)
        self.assertEqual(reversal['lines'][1]['account_code'], 'bank_interest_income')
        self.assertEqual(reversal['lines'][1]['debit_minor'], 2500)

    def test_transfer_requires_explicit_paired_legs(self):
        self.assertIsNone(build_journal({'type':'transfer','amount':'10'}))
        j = build_journal({'type':'cash_bank_transfer','amount':'10','amount_minor':1000,
                           'from_account':'cash','to_account':'bank'})
        self.assertEqual(j['debit_minor'], 1000)
        self.assertEqual(j['credit_minor'], 1000)
        self.assertEqual(j['lines'][0]['account_code'], 'bank')
        self.assertEqual(j['lines'][1]['account_code'], 'cash')
        self.assertIsNone(build_journal({'type':'cash_bank_transfer','amount':'-10','amount_minor':-1000,
                                         'from_account':'cash','to_account':'bank'}))

    def test_unknown_positive_transaction_uses_suspense_not_income(self):
        j = build_journal({'type':'unrecognized_legacy_type','amount':'42.25','amount_minor':4225,'account':'cash'})
        self.assertEqual(j['debit_minor'], 4225)
        self.assertEqual(j['credit_minor'], 4225)
        self.assertEqual(j['lines'][1]['account_code'], 'unclassified_receipt_suspense')

    def test_explicit_misc_income_posts_to_income_account(self):
        j = build_journal({'type':'misc_income','amount':'42.25','amount_minor':4225,'account':'cash'})
        self.assertEqual(j['lines'][1]['account_code'], 'other_income')

    def test_generic_outflow_debits_suspense_and_credits_cash(self):
        j = build_journal({'type':'asset_purchase','amount':'-42.25','amount_minor':-4225,'account':'cash'})
        self.assertEqual(j['debit_minor'], 4225)
        self.assertEqual(j['credit_minor'], 4225)
        self.assertEqual(j['lines'][0]['account_code'], 'unclassified_outflow_suspense')
        self.assertEqual(j['lines'][1]['account_code'], 'cash')

    def test_every_supported_financial_shape_is_balanced(self):
        samples = [
            {'type':'contribution','amount':'1','amount_minor':100},
            {'type':'interest','amount':'1','amount_minor':100},
            {'type':'penalty','amount':'1','amount_minor':100},
            {'type':'loan_disbursement','amount':'-1','amount_minor':-100,'principal':'-1'},
            {'type':'loan_repayment','amount':'1','amount_minor':100,'principal_repaid':'1'},
            {'type':'cash_bank_transfer','amount':'1','amount_minor':100,'from_account':'cash','to_account':'bank'},
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                journal = build_journal(sample)
                self.assertIsNotNone(journal)
                self.assertTrue(journal['balanced'])
                self.assertEqual(journal['debit_minor'], journal['credit_minor'])

    def test_negative_disbursement_is_balanced(self):
        j = build_journal({'type':'loan_disbursement','amount':'-100','amount_minor':-10000,'principal':'-100','account':'bank'})
        self.assertEqual(j['debit_minor'], j['credit_minor'])
        self.assertEqual(j['lines'][0]['account_code'], 'loan_receivable')

    def test_legacy_transaction_expense_is_not_misclassified_as_income(self):
        j = build_journal({'type':'expense','amount':'42.25','amount_minor':4225,'account':'bank','category':'Travel'})
        self.assertEqual(j['debit_minor'], 4225)
        self.assertEqual(j['credit_minor'], 4225)
        self.assertEqual(j['lines'][0]['account_code'], 'expense:travel')

if __name__ == '__main__': unittest.main()
