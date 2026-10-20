# Source form mapping

The input contract follows the `für Externe` tab of
[2026-03 Erstattungsformular Externe.xlsx](https://docs.google.com/spreadsheets/d/12UinisICGeN8uHZcaI7SBlEylE8C_VKL/edit?gid=551372684),
read on 2026-10-20. The workbook is a reference only; the application has no runtime
Google dependency and does not change or upload data to that workbook.

- C15, Vorname Nachname → `Claim.full_name`.
- C16, Anschrift → `Claim.postal_address`, multiline and international.
- C17, IBAN → normalized `Claim.iban`, validated with python-stdnum.
- C19, Erläuterung zur Erfüllung satzungsmäßiger Zwecke → `Claim.purpose`.
  Travel-enabled events also collect `trip_location`, `trip_start`, and `trip_end`.
- B27:F27, Nr., Name Geschäftspartner, Anlass/Zweck, Referenz/Rechnungsnr., Betrag →
  ordered `Expense` records with `merchant`, `purpose`, `receipt_reference`,
  `amount`, and `currency`. Totals use exact decimal arithmetic.
- B37, declaration → the verbatim German text in `portal/constants.py`, versioned
  in event configuration and snapshotted at acceptance.
- C41, Unterschrift → required private drawn/uploaded image, normalized and included
  in the PDF. E44, Datum → server-recorded submission date in Europe/Berlin.

The portal adds email for Finance correspondence, one to three receipt files per
expense, a review step, and privacy notice acknowledgment. It does not require
expense categories or per-expense dates absent from the reference form. At least
one receipt per row is the MVP's intake rule; other reimbursement mechanisms need
an explicit product change.

The source's internal KST and KTR values come from `Event.cost_center` and
`Event.cost_object`. `finance_note` maps the DATEV field; the claim UUID supplies
an internal reference. Participants cannot edit these values or the recipient.

The authoritative PDF contains the form details and a signature followed by all
receipt pages. Receipt PDF pages are flattened to preserve their visible content,
including form widgets and annotations, and support bounded compression. Their
original text layers and cryptographic PDF signatures are not preserved.
