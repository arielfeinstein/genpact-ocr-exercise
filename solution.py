"""Normalize and validate OCR-extracted invoice records."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from pprint import pprint
from typing import Literal


Severity = Literal["warning", "error"]


@dataclass(frozen=True)
class Flag:
    field: str
    reason: str
    severity: Severity


@dataclass
class FlaggedRecord:
    invoice_id: str | None
    amount: float | None
    date: date | None
    vendor: str | None
    flags: list[Flag] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return any(flag.severity == "error" for flag in self.flags)

    @property
    def reason(self) -> str:
        """Flatten structured flags using the public semicolon-delimited format."""
        parts: list[str] = []
        for flag in self.flags:
            parts.extend((flag.field, flag.reason, flag.severity))
        return "; ".join(parts)


_INVOICE_ID_RE = re.compile(r"^INV-[1-9][0-9]*$")
_AMOUNT_RE = re.compile(
    r"^-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?$"
)
_NUMERIC_DATE_RE = re.compile(
    r"^([0-9OS]{1,4})(?:\s*[-/.]\s*|\s+)([0-9OS]{1,2})"
    r"(?:\s*[-/.]\s*|\s+)([0-9OS]{1,4})$"
)
_MONTH_DATE_RE = re.compile(
    r"^([A-Za-z]+)\s+([0-9]{1,2})(?:\s*,\s*|\s+)([0-9]{4})$",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _prepare_string(raw_value: object, field_name: str) -> tuple[str | None, list[Flag]]:
    """Apply type and general missing-value validation."""
    if raw_value is None:
        return None, [Flag(field_name, "missing or unavailable value", "error")]
    if not isinstance(raw_value, str):
        return None, [Flag(field_name, "invalid field type", "error")]

    value = raw_value.strip()
    if not value or value.upper() in {"N/A", "NA"}:
        return None, [Flag(field_name, "missing or unavailable value", "error")]
    return value, []


def _clean_invoice_id(raw_value: object) -> tuple[str | None, list[Flag]]:
    """Normalize an invoice ID and return its normalized value and flags."""
    value, flags = _prepare_string(raw_value, "invoice_id")
    if value is None:
        return None, flags

    upper_value = value.upper()
    if upper_value != value:
        flags.append(Flag("invoice_id", "case normalized", "warning"))
    value = upper_value

    if re.search(r"\s", value):
        without_whitespace = re.sub(r"\s+", "", value)
        if _INVOICE_ID_RE.fullmatch(without_whitespace):
            value = without_whitespace
            flags.append(
                Flag("invoice_id", "internal whitespace removed", "warning")
            )

    if not _INVOICE_ID_RE.fullmatch(value):
        corrected = value.replace("O", "0").replace("S", "5")
        if corrected != value and _INVOICE_ID_RE.fullmatch(corrected):
            value = corrected
            flags.append(Flag("invoice_id", "OCR correction applied", "warning"))
        else:
            flags.append(Flag("invoice_id", "invalid invoice_id format", "error"))
            return None, flags
    return value, flags


def _remove_currency_indicator(value: str) -> str:
    """Remove at most one accepted leading or trailing currency indicator."""
    leading = re.fullmatch(r"(?is)(?:\$|USD)\s*(.*?)", value)
    if leading:
        return leading.group(1).strip()

    trailing = re.fullmatch(r"(?is)(.*?)\s*(?:\$|USD)", value)
    if trailing:
        return trailing.group(1).strip()
    return value


def _numeric_whitespace_is_removable(value: str) -> bool:
    """Return whether numeric-token whitespace can be removed unambiguously."""
    numeric_chars = "0123456789OS.,-"
    for match in re.finditer(r"\s+", value):
        if (
            match.start() == 0
            or match.end() == len(value)
            or value[match.start() - 1] not in numeric_chars
            or value[match.end()] not in numeric_chars
        ):
            return False

    candidate = re.sub(r"\s+", "", value)
    corrected_candidate = candidate.replace("O", "0").replace("S", "5")
    return _AMOUNT_RE.fullmatch(corrected_candidate) is not None


def _clean_amount(raw_value: object) -> tuple[float | None, list[Flag]]:
    """Normalize and validate a USD amount, returning its value and flags."""
    value, flags = _prepare_string(raw_value, "amount")
    if value is None:
        return None, flags

    value = _remove_currency_indicator(value)

    if re.search(r"\s", value) and _numeric_whitespace_is_removable(value):
        value = re.sub(r"\s+", "", value)
        flags.append(
            Flag("amount", "whitespace removed from numeric value", "warning")
        )

    if "O" in value or "S" in value:
        corrected = value.replace("O", "0").replace("S", "5")
        if _AMOUNT_RE.fullmatch(corrected):
            value = corrected
            flags.append(Flag("amount", "OCR correction applied", "warning"))

    if not _AMOUNT_RE.fullmatch(value):
        flags.append(Flag("amount", "invalid amount format", "error"))
        return None, flags

    amount = float(value.replace(",", ""))
    if not math.isfinite(amount):
        flags.append(Flag("amount", "invalid amount format", "error"))
        return None, flags
    if not -10_000_000.00 <= amount <= 10_000_000.00:
        flags.append(Flag("amount", "amount outside allowed range", "error"))
    return amount, flags


def _make_date(year: int, month: int, day: int) -> date | None:
    """Build a calendar date, returning ``None`` for impossible dates."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_date_without_correction(value: str) -> date | None:
    """Parse an accepted numeric or English month-name date without OCR repair."""
    numeric_match = _NUMERIC_DATE_RE.fullmatch(value)
    if numeric_match and not re.search(r"[OS]", value):
        first, second, third = numeric_match.groups()
        if len(first) == 4:
            return _make_date(int(first), int(second), int(third))
        if len(third) == 4 and len(first) <= 2:
            return _make_date(int(third), int(first), int(second))
        return None

    month_match = _MONTH_DATE_RE.fullmatch(value)
    if month_match:
        month_name, day_text, year_text = month_match.groups()
        month = _MONTHS.get(month_name.lower())
        if month is not None:
            return _make_date(int(year_text), month, int(day_text))
    return None


def _correct_numeric_date(value: str) -> str | None:
    """Apply supported OCR substitutions within numeric date components."""
    match = _NUMERIC_DATE_RE.fullmatch(value)
    if not match or not re.search(r"[OS]", value):
        return None

    pieces: list[str] = []
    previous_end = 0
    for group_number in range(1, 4):
        start, end = match.span(group_number)
        pieces.append(value[previous_end:start])
        pieces.append(value[start:end].replace("O", "0").replace("S", "5"))
        previous_end = end
    pieces.append(value[previous_end:])
    return "".join(pieces)


def _hundred_year_cutoff(processing_date: date) -> date:
    """Return the calendar-aware date exactly 100 years before processing."""
    target_year = processing_date.year - 100
    try:
        return processing_date.replace(year=target_year)
    except ValueError:
        # The only possible failure is February 29 in a non-leap target year.
        return processing_date.replace(year=target_year, day=28)


def _clean_date(raw_value: object) -> tuple[date | None, list[Flag]]:
    """Normalize and validate an invoice date, returning its value and flags."""
    value, flags = _prepare_string(raw_value, "date")
    if value is None:
        return None, flags

    parsed = _parse_date_without_correction(value)
    correction_applied = False

    if parsed is None:
        numeric_candidate = _correct_numeric_date(value)
        if numeric_candidate is not None:
            parsed = _parse_date_without_correction(numeric_candidate)
            correction_applied = parsed is not None

    if parsed is None:
        month_candidate = re.sub(
            r"\bJarn\b", "Jan", value, count=1, flags=re.IGNORECASE
        )
        if month_candidate != value:
            parsed = _parse_date_without_correction(month_candidate)
            correction_applied = parsed is not None

    if parsed is None:
        flags.append(Flag("date", "invalid date", "error"))
        return None, flags

    if correction_applied:
        flags.append(Flag("date", "OCR correction applied", "warning"))

    processing_date = date.today()
    if parsed > processing_date:
        flags.append(Flag("date", "future date", "warning"))
    elif parsed < _hundred_year_cutoff(processing_date):
        flags.append(Flag("date", "date older than 100 years", "warning"))
    return parsed, flags


def _clean_vendor(raw_value: object) -> tuple[str | None, list[Flag]]:
    """Normalize and validate a vendor name, returning its value and flags."""
    value, flags = _prepare_string(raw_value, "vendor")
    if value is None:
        return None, flags

    value = re.sub(r"\s+", " ", value)
    if len(value) > 100:
        flags.append(Flag("vendor", "vendor exceeds 100 characters", "error"))
    if not any(character.isalnum() for character in value):
        flags.append(
            Flag("vendor", "vendor contains no alphanumeric characters", "error")
        )
    return value, flags


def process_records(raw_records: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Normalize raw OCR invoice records.

    Returns ``(clean_records, flagged_records)``. Records with warnings appear in
    both lists, while records with an error appear only in ``flagged_records``.
    """
    if not isinstance(raw_records, list):
        raise TypeError("raw_records must be a list")
    if any(not isinstance(raw_record, dict) for raw_record in raw_records):
        raise TypeError("every raw record must be a dictionary")

    states: list[FlaggedRecord] = []
    invoice_id_map: dict[str, list[int]] = {}

    for source_index, raw_record in enumerate(raw_records):
        invoice_id, invoice_flags = _clean_invoice_id(raw_record.get("invoice_id"))
        amount, amount_flags = _clean_amount(raw_record.get("amount"))
        date_value, date_flags = _clean_date(raw_record.get("date"))
        vendor, vendor_flags = _clean_vendor(raw_record.get("vendor"))
        flags = invoice_flags + amount_flags + date_flags + vendor_flags

        states.append(
            FlaggedRecord(
                invoice_id=invoice_id,
                amount=amount,
                date=date_value,
                vendor=vendor,
                flags=flags,
            )
        )

        if invoice_id is not None:
            invoice_id_map.setdefault(invoice_id, []).append(source_index)

    for source_indexes in invoice_id_map.values():
        if len(source_indexes) > 1:
            for source_index in source_indexes:
                states[source_index].flags.append(
                    Flag("record", "duplicate invoice_id", "error")
                )

    clean_records: list[dict] = []
    flagged_records: list[dict] = []
    for state in states:
        normalized = {
            "invoice_id": state.invoice_id,
            "amount": state.amount,
            "date": state.date,
            "vendor": state.vendor,
        }
        if not state.has_error:
            # The absence of errors guarantees all required normalized values exist.
            clean_records.append(normalized)
        if state.flags:
            flagged_records.append({**normalized, "reason": state.reason})

    return clean_records, flagged_records


__all__ = ["process_records"]


if __name__ == "__main__":
    sample_records = [
        {"invoice_id": "INV-1001", "amount": "$1,200.00", "date": "2024-01-05", "vendor": "Acme Corp"},
        {"invoice_id": "INV-1002", "amount": "95O.5", "date": "01/06/2024", "vendor": "Beta LLC"},
        {"invoice_id": "INV-1003", "amount": "N/A", "date": "2024-01-07", "vendor": "Acme Corp"},
        {"invoice_id": "INV-1004", "amount": "2,340", "date": "Jan 8, 2024", "vendor": ""},
        {"invoice_id": "INV-1001", "amount": "$1,200.00", "date": "2024-01-05", "vendor": "Acme Corp"},
        {"invoice_id": "INV-1005", "amount": "-450.00", "date": "2024-13-40", "vendor": "Gamma Inc"},
        {"invoice_id": "INV-1006", "amount": " ", "date": "2024/01/09", "vendor": "Delta Co"},
        {"invoice_id": "INV-1007", "amount": "3200.00", "date": "2019-01-10", "vendor": "Acme Corp"}
    ]

    print("Inputs:")
    pprint(sample_records, sort_dicts=False, width=200)

    sample_clean_records, sample_flagged_records = process_records(sample_records)

    print("\nClean records:")
    pprint(sample_clean_records, sort_dicts=False, width=200)
    print("\nFlagged records:")
    pprint(sample_flagged_records, sort_dicts=False, width=200)
