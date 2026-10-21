"""Intake limits, formats and participant-facing messages.

The one source for the form template, its script and server validation. Limits come
from settings; every message that quotes a limit is rendered from the same value.
"""

import re
from dataclasses import asdict, dataclass
from decimal import Decimal

from django.conf import settings

CURRENCY_PRECISION = {"EUR": 2, "CHF": 2, "GBP": 2, "USD": 2, "JPY": 0}
# Whole units up to nine digits, an optional decimal comma or point, no grouping.
AMOUNT_PATTERN = r"\d{1,9}(?:[,.]\d{1,2})?"
EXPENSE_FIELD_LENGTHS = {"merchant": 200, "purpose": 500, "receipt_reference": 100}
# Signature attempts kept per draft, independent of the expense rows.
MAX_SIGNATURE_FILES = 3
RECEIPT_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
SIGNATURE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})


class IntakeError(Exception):
    """A participant-facing rejection: German message, optional field errors, HTTP status."""

    def __init__(self, message, *, errors=None, status=400):
        super().__init__(message)
        self.message, self.errors, self.status = message, errors or {}, status


@dataclass(frozen=True)
class Limits:
    receipt_bytes: int
    submission_bytes: int
    signature_bytes: int
    expenses: int
    files_per_expense: int
    pdf_pages: int
    claim_pages: int

    @property
    def draft_files(self):
        return self.expenses * self.files_per_expense + MAX_SIGNATURE_FILES


def limits():
    return Limits(
        receipt_bytes=settings.MAX_RECEIPT_BYTES,
        submission_bytes=settings.MAX_SUBMISSION_BYTES,
        signature_bytes=settings.MAX_SIGNATURE_BYTES,
        expenses=settings.MAX_EXPENSES,
        files_per_expense=settings.MAX_FILES_PER_EXPENSE,
        pdf_pages=settings.MAX_PDF_PAGES,
        claim_pages=settings.MAX_CLAIM_PAGES,
    )


def megabytes(size):
    return f"{size / 1_000_000:g} MB"


STATIC_MESSAGES = {
    "normalized_size_limit": "Die verarbeitete Datei ist zu groß. Bitte exportiere sie mit geringerer Auflösung.",
    "document_sandbox_unavailable": "Die Sicherheitsprüfung ist vorübergehend nicht verfügbar. Bitte kontaktiere uns.",
    "unsupported_type": "Bitte lade eine PDF-, JPG- oder PNG-Datei hoch.",
    "invalid_document": "Die Datei konnte nicht gelesen werden. Bitte exportiere sie erneut.",
    "encrypted_pdf": "Die PDF ist passwortgeschützt. Bitte lade eine ungeschützte Kopie hoch.",
    "pdf_active_content": "Diese PDF enthält nicht unterstützte aktive Inhalte. Bitte exportiere sie als einfache PDF.",
    "empty_signature": "Die Unterschrift ist leer. Bitte unterschreibe erneut.",
    "malware_detected": "Die Datei hat die Sicherheitsprüfung nicht bestanden.",
    "scanner_unavailable": "Die Sicherheitsprüfung ist verzögert. Bitte warte oder versuche es später erneut.",
    "upload_unchecked": "Die Datei konnte nicht geprüft werden. Bitte lade sie erneut hoch.",
}


def messages():
    current = limits()
    return {
        **STATIC_MESSAGES,
        "pdf_pages": f"Eine PDF darf höchstens {current.pdf_pages} Seiten enthalten.",
        "receipt_too_large": f"Eine Belegdatei darf höchstens {megabytes(current.receipt_bytes)} groß sein.",
        "receipt_size": f"Jede Belegdatei muss zwischen 1 Byte und {megabytes(current.receipt_bytes)} groß sein.",
        "signature_too_large": f"Die Unterschrift darf höchstens {megabytes(current.signature_bytes)} groß sein.",
        "file_too_large": (
            f"Die Unterschrift darf höchstens {megabytes(current.signature_bytes)}, "
            f"ein Beleg höchstens {megabytes(current.receipt_bytes)} groß sein."
        ),
        "submission_too_large": (
            f"Alle Belege zusammen dürfen höchstens {megabytes(current.submission_bytes)} groß sein."
        ),
        "claim_pages": f"Alle Belege zusammen dürfen höchstens {current.claim_pages} Seiten enthalten.",
        "claim_too_large": (
            f"Die Belege überschreiten {megabytes(current.submission_bytes)} "
            f"oder {current.claim_pages} Seiten."
        ),
        "expense_count": f"Bitte füge 1 bis {current.expenses} Auslagenpositionen hinzu.",
        "files_per_expense": f"Jede Position benötigt 1 bis {current.files_per_expense} Belegdateien.",
        "files_per_expense_exceeded": (
            f"Pro Position sind höchstens {current.files_per_expense} Belegdateien erlaubt."
        ),
        "upload_hint": (
            f"PDF, JPG oder PNG · bis {megabytes(current.receipt_bytes)} pro Datei · "
            f"{megabytes(current.submission_bytes)} insgesamt · "
            f"höchstens {current.claim_pages} Belegseiten"
        ),
        "receipt_hint": (
            f"Bis zu {current.files_per_expense} Dateien. "
            "Die Reihenfolge kannst du anschließend ändern."
        ),
        "signature_hint": (
            "Mit Finger, Stift oder Maus unterschreiben oder ein Bild hochladen. "
            f"JPG/PNG, höchstens {megabytes(current.signature_bytes)}."
        ),
    }


def message(code, default=None):
    return messages().get(code, default)


def client_config(currencies):
    """Everything the form script needs to apply the same limits and formats."""
    return {
        "limits": asdict(limits()),
        "amount_pattern": AMOUNT_PATTERN,
        "currency_precision": {code: CURRENCY_PRECISION[code] for code in currencies},
        "messages": messages(),
    }


def parse_amount(value, currency):
    """An exact positive Decimal in the currency's precision, or ValueError."""
    if not isinstance(value, str) or not re.fullmatch(AMOUNT_PATTERN, value.strip()):
        raise ValueError("Betrag ohne Tausendertrennzeichen eingeben, z. B. 12,50.")
    amount = Decimal(value.strip().replace(",", "."))
    precision = CURRENCY_PRECISION.get(currency)
    if precision is None or -amount.as_tuple().exponent > precision or amount <= 0:
        raise ValueError("Bitte gib einen positiven Betrag mit passender Währungsgenauigkeit ein.")
    return amount
