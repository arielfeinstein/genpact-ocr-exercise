"""Pytest coverage for the OCR invoice processing module."""

import copy
from datetime import date

import pytest

import solution


def record(
    invoice_id="INV-1",
    amount="1",
    date_value="2024-01-01",
    vendor="Vendor",
):
    """Create a complete raw record with convenient defaults."""
    return {
        "invoice_id": invoice_id,
        "amount": amount,
        "date": date_value,
        "vendor": vendor,
    }


class FixedDate(date):
    """Date replacement that makes date-sensitive tests deterministic."""

    @classmethod
    def today(cls):
        """Return the fixed processing date used by the test suite."""
        return cls(2026, 2, 28)


def test_required_sample(monkeypatch):
    """The required sample produces two clean and seven flagged records."""
    raw_records = [
        record("INV-1001", "$1,200.00", "2024-01-05", "Acme Corp"),
        record("INV-1002", "95O.5", "01/06/2024", "Beta LLC"),
        record("INV-1003", "N/A", "2024-01-07", "Acme Corp"),
        record("INV-1004", "2,340", "Jan 8, 2024", ""),
        record("INV-1001", "$1,200.00", "2024-01-05", "Acme Corp"),
        record("INV-1005", "-450.00", "2024-13-40", "Gamma Inc"),
        record("INV-1006", " ", "2024/01/09", "Delta Co"),
        record("INV-1007", "3200.00", "2019-01-10", "Acme Corp"),
    ]
    monkeypatch.setattr(solution, "date", FixedDate)

    clean, flagged = solution.process_records(raw_records)

    assert [item["invoice_id"] for item in clean] == ["INV-1002", "INV-1007"]
    assert len(flagged) == 7
    assert flagged[0]["reason"] == "record; duplicate invoice_id; error"
    assert flagged[1]["reason"] == "amount; OCR correction applied; warning"


def test_input_validation_and_immutability():
    """Invalid containers raise TypeError and valid input remains unchanged."""
    with pytest.raises(TypeError):
        solution.process_records({})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        solution.process_records([record(), None])  # type: ignore[arg-type]

    raw = [record() | {"extra": ["untouched"]}]
    original = copy.deepcopy(raw)
    solution.process_records(raw)

    assert raw == original


def test_non_string_fields_are_errors_and_are_not_coerced():
    """Non-string field values are rejected without implicit conversion."""
    clean, flagged = solution.process_records(
        [{"invoice_id": 1, "amount": 2, "date": 3, "vendor": 4}]
    )

    assert clean == []
    assert flagged[0]["reason"] == (
        "invoice_id; invalid field type; error; amount; invalid field type; error; "
        "date; invalid field type; error; vendor; invalid field type; error"
    )
    assert flagged[0]["invoice_id"] is None


def test_invoice_id_normalization_and_flag_order():
    """Invoice IDs are canonicalized and their flags retain action order."""
    clean, flagged = solution.process_records([record(" i n v - 1 ")])

    assert clean[0]["invoice_id"] == "INV-1"
    assert flagged[0]["reason"] == (
        "invoice_id; case normalized; warning; "
        "invoice_id; internal whitespace removed; warning"
    )
    assert solution.process_records([record("INV-0")])[0] == []


def test_amount_formats_corrections_and_range():
    """Accepted amount forms normalize and invalid or extreme values are flagged."""
    values = [
        "$1,200.00",
        "USD 1200",
        "1200 usd",
        "1 200.00",
        "-450.00",
        "95O.5",
    ]
    rows = [
        record(f"INV-{index + 1}", value)
        for index, value in enumerate(values)
    ]

    clean, flagged = solution.process_records(rows)

    assert [row["amount"] for row in clean] == [
        1200.0,
        1200.0,
        1200.0,
        1200.0,
        -450.0,
        950.5,
    ]
    assert len(flagged) == 2
    assert "whitespace removed" in flagged[0]["reason"]
    assert "OCR correction applied" in flagged[1]["reason"]

    for invalid in ("12,34", "+1", "1e3", "(1)", "12.00.50", "EUR 1"):
        assert solution.process_records([record(amount=invalid)])[0] == []

    _, range_flagged = solution.process_records([record(amount="10000000.01")])
    assert range_flagged[0]["amount"] == 10_000_000.01
    assert "amount outside allowed range" in range_flagged[0]["reason"]

    _, non_finite = solution.process_records([record(amount="9" * 400)])
    assert non_finite[0]["amount"] is None
    assert "invalid amount format" in non_finite[0]["reason"]


def test_date_formats_correction_and_suspicion_boundaries(monkeypatch):
    """Date formats, OCR repair, age cutoff, and future checks follow the spec."""
    rows = [
        record(f"INV-{index + 1}", date_value=value)
        for index, value in enumerate(
            [
                "2024/01/09",
                "2024.01.09",
                "01/06/2024",
                "Jan 8, 2024",
                "01/0S/2024",
                "Jarn 5 2024",
                "1926-02-28",
                "1926-02-27",
                "2026-03-01",
            ]
        )
    ]
    monkeypatch.setattr(solution, "date", FixedDate)

    clean, flagged = solution.process_records(rows)

    assert len(clean) == len(rows)
    assert clean[0]["date"] == date(2024, 1, 9)
    assert [item["invoice_id"] for item in flagged] == [
        "INV-5",
        "INV-6",
        "INV-8",
        "INV-9",
    ]
    assert "date older than 100 years" in flagged[2]["reason"]
    assert "future date" in flagged[3]["reason"]


def test_invalid_semantic_values_retain_successful_normalization():
    """Semantically invalid values remain available in flagged output."""
    vendor = "!" * 101

    _, flagged = solution.process_records(
        [record(amount="10000001", vendor=vendor)]
    )

    assert flagged[0]["amount"] == 10_000_001.0
    assert flagged[0]["vendor"] == vendor
    assert "vendor exceeds 100 characters" in flagged[0]["reason"]
    assert "vendor contains no alphanumeric characters" in flagged[0]["reason"]


def test_valid_invoice_id_participates_despite_other_field_errors():
    """A parsed invoice ID participates even when another field is invalid."""
    clean, flagged = solution.process_records(
        [record("INV-20"), record("INV-20", amount="bad")]
    )

    assert clean == []
    assert len(flagged) == 2
    assert flagged[0]["reason"] == "record; duplicate invoice_id; error"
    assert flagged[1]["reason"] == (
        "amount; invalid amount format; error; "
        "record; duplicate invoice_id; error"
    )


def test_warning_is_updated_not_duplicated_when_duplicate_found():
    """Duplicate reconciliation appends its error to an existing warning entry."""
    clean, flagged = solution.process_records(
        [record("inv-30"), record("INV-30")]
    )

    assert clean == []
    assert len(flagged) == 2
    assert flagged[0]["reason"] == (
        "invoice_id; case normalized; warning; "
        "record; duplicate invoice_id; error"
    )
