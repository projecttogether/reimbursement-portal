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
  form has purpose options, the participant chooses one and may add
  `purpose_details`; otherwise it is free text. Travel details (`trip_destination`,
  "Zielort", and `trip_start`/`trip_end`) follow the template's travel mode and are
  required for mileage.
- B27:F27, Nr., Name Geschäftspartner, Anlass/Zweck, Referenz/Rechnungsnr., Betrag →
  ordered `Expense` records with `merchant`, `purpose`, optional
  `receipt_reference`, and `amount` in EUR with at most two cent digits. Mileage
  rows store date, route, distance, return trip and rate instead of a merchant;
  their amount is calculated on the server. Totals use exact decimal arithmetic.
- B37, declaration → the verbatim German text in `portal/constants.py`, the default
  of each template, versioned and snapshotted at acceptance.
- C41, Unterschrift → required private drawn/uploaded image, normalized and included
  in the PDF. E44, Datum → server-recorded submission date in Europe/Berlin.

The portal adds email for Finance correspondence, one to three receipt files per
expense, mileage rows, a review step, and privacy notice acknowledgment. It does not
require expense categories or per-expense dates absent from the reference form.
Receipt rows need at least one receipt; mileage rows need none.

The source's internal KST and KTR values, and any other internal value such as an
ID, are template fields whose values each form sets (`Form.field_values`). They are
snapshotted with the claim and printed in the PDF's header box; the claim UUID
supplies an internal reference. Participants cannot see or edit these values or
the recipient.

The authoritative PDF contains the form details and a signature followed by all
receipt pages. Receipt PDF pages are flattened to preserve their visible content,
including form widgets and annotations, and support bounded compression. Their
original text layers and cryptographic PDF signatures are not preserved.
