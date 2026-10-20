# PT Reimbursement Portal implementation plan

Status: MVP implemented and verified locally on 2026-10-20. PostgreSQL integration tests, desktop/mobile browser flows, both signature paths, PDF page-content/order checks, local email attachment verification, and the production image build pass. Production configuration and the real MailerSend/Moss route smoke test remain release requirements.

## Confirmed MVP

Build a German, mobile-friendly reimbursement form for external participants. Accept private receipts and a signature, generate one complete PDF, compress it to the configured output limit, and send it through MailerSend to the configured Moss invoice inbox. Moss invoice-inbox forwarding is an established PT workflow.

- Keep the upload limits: 10,000,000 bytes per receipt and 50,000,000 bytes of receipts per submission.
- The generated merged PDF is the authoritative record. Preserve the visible contents of all receipt pages, their order, the form details, and the signature.
- No staff login, Django admin, staff pages, participant accounts, payment integration, AI processing, or correction portal in the MVP.
- Return an on-screen claim reference after durable local acceptance. A separate participant email is outside this narrowed MVP; the email address supports Finance correspondence.
- Multiple events use configuration. Finance continues to own approval, accounting, and payment in its existing workflow.

## Input contract from the existing form

Source: [2026-03 external reimbursement form](https://docs.google.com/spreadsheets/d/12UinisICGeN8uHZcaI7SBlEylE8C_VKL/edit?gid=551372684), tab `für Externe`. The linked file is an Excel workbook opened in Google Sheets. It was read without changing or converting the source.

Participant fields follow that form:

- Vorname Nachname, Anschrift, IBAN.
- Erläuterung zur Erfüllung satzungsmäßiger Zwecke; for travel, also Ort and Zeitraum.
- Expense rows with Nr., Name Geschäftspartner, Anlass/Zweck, Referenz/Rechnungsnr., and Betrag.
- Sum, signature, and submission date.
- The source declaration: "Hiermit bestätige ich, dass mir die geltend gemachten Aufwendungen tatsächlich und notwendigerweise ausschließlich für das Geschäft und zur Erfüllung der satzungsmäßigen Zwecke der ProjectTogether gGmbH entstanden sind."

Add participant email for correspondence, receipt uploads for each row, and the approved privacy notice. KST, KTR, DATEV-Vermerkfeld, and the internal reference are event configuration or generated metadata, not participant input. Do not invent a required expense category or expense-date field absent from the source. Initial event currency is EUR; other explicitly configured supported currencies produce separate totals without conversion.

## Architecture

- Django 5.2 LTS, Python 3.13, server-rendered templates, local CSS and JavaScript.
- PostgreSQL in deployment. SQLite supports local single-worker development; PostgreSQL tests verify row-lock behavior.
- One application worker using database jobs, short row locks, leases, bounded retries, and deterministic job keys. No Redis or separate frontend.
- WeasyPrint renders the summary. PDFium renders uploaded PDF pages including visible form fields and annotations. Pillow normalizes images; pypdf assembles the generated pages and bookmarks.
- Private persistent filesystem storage behind a small storage module. Off-host encrypted database/file backups are an operations requirement. Local acknowledgment does not guarantee survival of total host failure.
- MailerSend HTTPS API. Local development captures private `.eml` files and sends no real email.
- Docker Compose / existing Coolify deployment, separate web, worker, PostgreSQL, and ClamAV services.
- No public attachment URL or download endpoint. Operational inspection, configuration, retry, reconciliation, and deletion use management commands through authorized host access.

## Participant and acceptance flow

1. Open an active event link. Display event instructions, deadline, limits, Finance contact, and privacy notice.
2. Enter personal information, purpose, and travel details where configured.
3. Add, remove, or reorder expense rows. Every row has a stable UUID, independent of its position, and one to three receipt files.
4. Upload each file through a session-bound, CSRF-protected endpoint. Check quotas and ownership before accepting it for asynchronous validation and scanning.
5. Review all entered data, row totals, and receipts. Confirm the source declaration, provide a drawn or uploaded signature, and acknowledge the privacy notice. Returning to edit clears the signature and declaration.
6. Final submission locks the draft, verifies the event revision/deadline, all files and ownership, exact decimal amounts, IBAN structure/checksum, and source-form fields. One transaction creates an immutable claim and render job.
7. Repeated identical POSTs return the same claim; different content using an accepted token is rejected. Already accepted retries remain valid if the event subsequently closes or changes.
8. Return the session-authorized receipt page with an opaque reference. PDF creation and delivery proceed in the background. Never describe submission or email delivery as reimbursement approval.

Typed participant data stays in browser memory before acceptance. No localStorage, third-party analytics, session replay, or sensitive request-body logging. Reloading or closing an unsubmitted form can lose typed values; warn before leaving.

## File validation and limits

- Maximum 30 expense rows, three receipts per row, 30 pages per individual PDF, 100 receipt pages per claim.
- PDF, JPG/JPEG, PNG only. Reject encrypted/corrupt PDFs, unsupported active content, SVG, HEIC, archives, and images over 40 million decoded pixels.
- Signature: drawn PNG or uploaded PNG/JPEG, maximum 1,000,000 bytes. Normalize orientation and transparency, and reject an empty signature image.
- Scan source files with private ClamAV before marking them ready. Scanner failure leaves uploads pending; never treat an outage as a clean scan.
- Enforce per-file and aggregate quotas in application code and bound proxy request sizes. Limit active drafts and expire abandoned uploads.
- Run document parsing/conversion in a subprocess with stripped credentials, time/memory/CPU/file-size limits, private temporary directories, and an allowlist for summary assets.

## Authoritative PDF and compression

Include participant information, purpose, trip information, KST/KTR and configured Finance metadata, claim reference/time, all expense fields, exact per-currency totals, declaration, signature, and declaration/notice versions. Repeat table headings and handle long Unicode text without clipping.

Append every receipt page by expense order and within-row file order. Add bookmarks linking pages to expense references. Render source PDF pages with their visible fields and annotations into flattened receipt pages; the generated summary remains selectable text. Original PDF cryptographic signature guarantees and source PDF text layers are not preserved in the flattened receipt pages.

Keep the existing upload limits and compress automatically. Assemble the complete output, measure serialized bytes, and progressively reduce receipt image resolution/quality until it fits the 10,000,000-byte final limit. Each pass must retain every page. Reopen the final PDF and verify its page count, hash, and size before queuing delivery.

Bound compression at 72 DPI and JPEG quality 50 rather than silently destroying readability. A pathological claim that still cannot fit becomes an explicit processing exception for technical recovery. Never drop pages, send a partial bundle, bypass the size limit, or create a public download link. Pilot Finance checks must approve the readability of compressed examples.

## Delivery and recovery

Use the immutable destination/configuration snapshot and original ready PDF for retries. Event destinations must be in the approved recipient list; staging has a separate enforced test-recipient allowlist. Keep credentials, payloads, IBAN, and attachments out of general logs.

Record each send attempt before calling MailerSend. Interpret message IDs, paused acceptance, suppression warnings, throttling, and rejected payloads. Connection failures known to precede acceptance can retry with bounded backoff. A timeout, server error, or worker death around transmission becomes `send_unknown`; do not automatically resend it.

Use webhook version 2. Verify signatures over the raw body, handle the separately signed setup ping, persist minimal deduplicated events, and correlate them by message ID or opaque attempt tag. Tolerate callbacks arriving before the HTTP response commits and callbacks arriving out of order. Provider acceptance, recipient-server delivery, and actual Finance/Moss processing remain separate concepts.

Provide commands to inspect safe status, retry failed processing, intentionally resend with an audited reason and duplicate-risk acknowledgment, reconcile known message IDs, and preview/execute due purges. Unknown sends without message IDs require provider activity lookup before an intentional retry.

## Retention and operations

- Expire abandoned drafts/uploads after 24 hours.
- Proposed successful-claim content retention: seven days after recipient-server delivery. Finance must confirm that this is an accepted handoff before production deletion is enabled.
- Failed/ambiguous claims have a 30-day resolution target, with advance operational alerts. Do not silently delete unresolved claims; the operator must resolve, securely hand off, or obtain an approved retention extension.
- Purge participant details, IBAN, purpose, expenses, signatures, original/normalized receipts, work files, generated PDFs, local email copies, and associated provider details. A recoverable `purging` state permits crash recovery.
- Keep only reference/time/status for an additional proposed 30-day operational period, then delete it. Confirm these periods with Finance/Legal before real-data launch.
- The worker performs periodic recovery, orphan cleanup, and deletion; heartbeat monitoring is separate from web liveness. Use an independent alert channel for queue age, failed/unknown sends, processing errors, scanner outages, disk capacity, and failed purges.
- Preserve matching database/file backups, protect backup keys outside Git, and test restoration. Reconcile interrupted sends and apply overdue deletions before resuming a restored deployment. Maintain evidence of exceptional deletions outside backup rollback.

## Verification and release

Automated tests use synthetic names, a test IBAN, generated signatures, and dummy receipts. Cover form validation, exact amounts, upload ownership, token replays and races, current event revisions/deadlines, corrupt/active/encrypted PDFs, filled PDF appearances, receipt order, actual compression, bounded failures, signed/duplicate/early webhooks, ambiguous sends, deletion, and PostgreSQL locking.

Verify the full participant flow at desktop and mobile widths, keyboard errors, signature upload/drawing, row/file reordering, and local captured PDF delivery. Test a realistic event burst before setting service-time commitments.

Before real participant data:

- Configure the approved host, TLS/proxy rules, encrypted persistent storage/backups, resource limits, independent monitoring, and an accountable technical operator.
- Set verified sender, reply-to, MailerSend token/webhook secret, Moss destination, KST/KTR, deadline, support contact, and approved privacy text.
- Confirm processors, retention/deletion handoff, signature/declaration requirements, and the acceptable complete-host-loss window with the relevant owners.
- Run a controlled complete dummy claim through the real MailerSend/Moss route, with Finance checking content and compressed readability. This is a release smoke test of the established workflow.
- Demonstrate backup restoration, purge recovery, and manual handling of an ambiguous send.

AI checks, a staff portal, participant accounts, self-service corrections, original-file archiving, and payment automation remain outside this MVP.
