from app.journal import build_journal
from app.accounting_engine import profit_components, to_minor


def test_investment_purchase_is_asset_not_expense():
    journal = build_journal({"type": "investment_disbursement", "amount_minor": -to_minor(25000), "account": "bank"})
    assert journal["balanced"] is True
    assert journal["debit_minor"] == to_minor(25000)
    assert journal["lines"] == [
        {"account_code": "investment_asset", "debit_minor": to_minor(25000), "credit_minor": 0},
        {"account_code": "bank", "debit_minor": 0, "credit_minor": to_minor(25000)},
    ]
    assert profit_components({"type": "investment_disbursement", "amount_minor": -to_minor(25000)})["income_total"] == 0


def test_full_investment_sale_counts_only_realized_profit():
    tx = {"type": "investment_return", "amount_minor": to_minor(28000),
          "investment_capital_minor": to_minor(25000),
          "investment_realized_profit_minor": to_minor(3000),
          "investment_realized_loss_minor": 0, "account": "cash"}
    journal = build_journal(tx)
    parts = profit_components(tx)
    assert journal["balanced"] is True
    assert journal["debit_minor"] == to_minor(28000)
    assert parts["income_total"] == to_minor(3000)


def test_investment_sale_loss_reduces_profit():
    tx = {"type": "investment_return", "amount_minor": to_minor(22000),
          "investment_capital_minor": to_minor(25000),
          "investment_realized_profit_minor": 0,
          "investment_realized_loss_minor": to_minor(3000), "account": "bank"}
    journal = build_journal(tx)
    parts = profit_components(tx)
    assert journal["balanced"] is True
    assert parts["income_total"] == -to_minor(3000)


def test_partial_capital_return_is_not_profit():
    tx = {"type": "investment_return", "amount_minor": to_minor(10000),
          "investment_capital_minor": to_minor(10000),
          "investment_realized_profit_minor": 0,
          "investment_realized_loss_minor": 0, "account": "cash"}
    assert build_journal(tx)["balanced"] is True
    assert profit_components(tx)["income_total"] == 0


def test_group_profit_bucket_includes_realized_investment_gain_and_loss():
    from app.accounting_engine import net_profit_from_buckets_minor
    assert net_profit_from_buckets_minor(investment_profit=3000, investment_loss=-500) == to_minor(2500)


def test_reversing_realized_investment_profit_offsets_original_profit():
    original = {"type": "investment_return", "amount_minor": to_minor(28000),
                "investment_capital_minor": to_minor(25000),
                "investment_realized_profit_minor": to_minor(3000),
                "investment_realized_loss_minor": 0, "account": "bank"}
    reversal = {"type": "reversal", "original_type": "investment_return", "reversal_of": "source-id",
                "amount_minor": -original["amount_minor"],
                "investment_realized_profit_minor": original["investment_realized_profit_minor"],
                "investment_realized_loss_minor": 0, "account": "bank"}
    assert profit_components(original)["income_total"] == to_minor(3000)
    assert profit_components(reversal)["income_total"] == -to_minor(3000)


def test_reversing_realized_investment_loss_offsets_original_loss():
    original = {"type": "investment_return", "amount_minor": to_minor(22000),
                "investment_capital_minor": to_minor(25000),
                "investment_realized_profit_minor": 0,
                "investment_realized_loss_minor": to_minor(3000), "account": "cash"}
    reversal = {"type": "reversal", "original_type": "investment_return", "reversal_of": "source-id",
                "amount_minor": -original["amount_minor"],
                "investment_realized_profit_minor": 0,
                "investment_realized_loss_minor": original["investment_realized_loss_minor"], "account": "cash"}
    assert profit_components(original)["income_total"] == -to_minor(3000)
    assert profit_components(reversal)["income_total"] == to_minor(3000)


def test_investment_purchase_journal_can_be_exactly_reversed():
    from app.journal import reverse_journal
    original = build_journal({"type": "investment_disbursement", "amount_minor": -to_minor(25000), "account": "bank"})
    reversed_journal = reverse_journal(original)
    assert reversed_journal["balanced"] is True
    assert reversed_journal["lines"] == [
        {"account_code": "investment_asset", "debit_minor": 0, "credit_minor": to_minor(25000)},
        {"account_code": "bank", "debit_minor": to_minor(25000), "credit_minor": 0},
    ]


def test_investment_return_journal_can_be_exactly_reversed():
    from app.journal import reverse_journal
    original = build_journal({"type": "investment_return", "amount_minor": to_minor(28000),
                              "investment_capital_minor": to_minor(25000),
                              "investment_realized_profit_minor": to_minor(3000),
                              "investment_realized_loss_minor": 0, "account": "cash"})
    reversed_journal = reverse_journal(original)
    assert reversed_journal["balanced"] is True
    assert reversed_journal["debit_minor"] == to_minor(28000)
    assert reversed_journal["credit_minor"] == to_minor(28000)
