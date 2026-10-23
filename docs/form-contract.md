# Source form mapping

The input contract follows the `für Externe` tab of
[2026-03 Erstattungsformular Externe.xlsx](https://docs.google.com/spreadsheets/d/12UinisICGeN8uHZcaI7SBlEylE8C_VKL/edit?gid=551372684),
read on 2026-10-20. The workbook is a reference only; the application has no runtime
Google dependency and does not change or upload data to that workbook.

- C15, Vorname Nachname → `Claim.full_name`: letters in any script with spaces,
  hyphens, apostrophes and dots; no digits or other symbols.
- C16, Anschrift → `Claim.street`, `postal_code`, `city` and ISO `country`. German
  postcodes have five digits, Austrian and Swiss four; other countries accept up to
  12 letters, digits, spaces and hyphens.
- Kontoinhaber:in → `Claim.account_holder`, prefilled with the name; also `&` and
  `,` for joint accounts.
- C17, IBAN → normalized `Claim.iban`: country format and checksum validated with
  python-stdnum, any IBAN country. `Claim.bic` is validated when given and required
  for IBANs outside the SEPA countries.
- C19, Erläuterung zur Erfüllung satzungsmäßiger Zwecke → `Claim.purpose`. When the
  form has one purpose option, it is fixed text; several options require a choice
  and may have `purpose_details`. Without options it is free text. The participant
  label is "Anlass/Bezug zum Projekt oder der Veranstaltung". Every expense inherits
  this purpose on the server. Travel details (`trip_destination`, "Zielort", and
  `trip_start`/`trip_end`) follow the template's travel mode for receipt-only claims.
  Mileage collects its own route and dates, so these claim-level questions are hidden
  and ignored when mileage is present.
- B27:F27, Nr., Name Geschäftspartner, Anlass/Zweck, Referenz/Rechnungsnr., Betrag →
  ordered `Expense` records with `merchant`, `purpose`, optional
  `receipt_reference`, and `amount` in EUR with at most two cent digits. Mileage
  rows store outbound date, vehicle registration, route, distance, return trip and
  rate instead of a merchant. A return trip requires a return date on or after the
  outbound date; one-way trips discard any return date. Their amount is calculated
  on the server. Totals use exact decimal arithmetic.
- B37, declaration → the verbatim German text in `portal/constants.py`, the default
  of each template, versioned and snapshotted at acceptance.
- C41, Unterschrift → required private drawn/uploaded image, normalized and included
  in the PDF. E44, Datum → server-recorded submission date in Europe/Berlin.

The portal adds email for Finance correspondence, one to three receipt files per
expense, mileage rows, a review step, and privacy notice acknowledgment. It does not
require expense categories or per-expense dates absent from the reference form.
The form starts with no positions. Participants choose "Auslage mit Beleg" or
"Kilometererstattung" and can combine both or submit mileage alone. Receipt rows
need at least one receipt; one file is sufficient and additional files are optional.
Mileage rows need none.

The source's internal KST and KTR values, and any other internal value such as an
ID, are template fields whose values each form sets (`Form.field_values`). They are
snapshotted with the claim and printed in the PDF's header box. Participants cannot
see or edit these internal values or the recipient. The claim UUID
remains the internal identifier and receipt-page URL key. New claims receive a
stored, unique bookkeeping reference `Erst/YYYY-MM-DD/form-slug/surname/000001`.
The date is the Berlin submission date, the surname is the URL-safe last word of
the full name, and the suffix is a database-locked global sequence. The reference
never changes after acceptance and is used in confirmation, admin search, PDFs and
emails. Email filenames replace slashes with underscores. Existing claims keep
their UUID reference. The readable reference contains a name and is cleared at
content purge; the counter contains no participant data and is retained to prevent
number reuse. Operator commands accept either reference or UUID.

The authoritative PDF contains the form details and a signature followed by all
receipt pages. Receipt PDF pages are flattened to preserve their visible content,
including form widgets and annotations, and support bounded compression. Their
original text layers and cryptographic PDF signatures are not preserved.

The intake contract version participates in the form revision. Drafts opened before
this simplified intake release must reload before uploading or submitting; accepted
claims still replay with their original reference.
