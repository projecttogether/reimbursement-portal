# Domain context

The language this codebase uses for reimbursement intake and delivery, and the module
that owns each concept. Keep it current when a concept gains or changes a home.

## Glossary

**Template** (Vorlage): a type of form (`FormTemplate`). It sets the intro text,
declaration, travel mode (off/optional/required) and whether mileage is allowed at
which rate. Its **template fields** define the internal values every form of the
template sets, such as KST, KTR or an ID; participants never see them, the PDF does.

**Form** (Formular): a published reimbursement form at `/f/<slug>/`, created from a
template by its owners. It holds the internal values, purpose options, instructions,
recipient, contact and opening times.

**Revision**: a digest of what a participant agrees to and is validated against: the
declaration, the privacy notice version, the purpose options, the travel mode and the
mileage rate (`Form.revision`). It is derived, never stored on the form, so no save
path can forget it. Other edits, such as wording, opening times, internal values and
contacts, leave open drafts working; the claim snapshot records them at acceptance.

**Start page form**: the one form marked *Startseite* (`Form.primary`), chosen by
template admins. `/` redirects to it while it is open, and otherwise shows a short
landing text.

**Privacy notice**: the one app-wide, versioned text. A change is a new version;
claims record the version they acknowledged.

**Admin roles** (`portal/roles.py`): *template admins* and *form editors*, granted at
every PT Login sign-in from the Keycloak `groups` claim (`portal/auth.py`).

**Form access**: template admins manage every form; form editors manage the forms
they own. Everyone sees the claims of the forms they manage. The admin lists, object
checks, search and the owner preview all ask `roles.managed_forms` and
`roles.visible_claims`.

**Draft**: one participant's unsubmitted form, bound to a browser session and a secret
token. A draft is *accepting* while it is unsubmitted, its form is open, and its
revision matches the form's (`services/drafts.py`).

**Claim**: the immutable record created when a draft is accepted. Its states are
`received → rendering → ready`, or `processing_failed`, then `purging → purged`.
After delivery its PDF is removed at `pdf_due` (7 days) and all content at
`retention_due` (30 days after submission) (`services/retention.py`).

**Expense**: one row of a claim, in EUR. A *receipt* row has merchant, purpose,
optional reference, amount and one to three receipts. A *mileage* row has date,
route, one-way distance and return trip; its amount is distance × the template's
rate, calculated on the server, and proofs are optional.

**Upload**: a receipt (attached to an expense) or a signature. Uploads are `pending`
until scanned and normalized, then `ready` or `failed`. An upload is *busy* while its
validation runs or an execution workspace holds it (`services/uploads.py`).

**Intake limits**: the file counts, sizes, page limits, amount and distance formats
and participant messages that the form, its script and server validation
share (`portal/intake.py`). An **IntakeError** is a participant-facing rejection.

**Authoritative PDF**: the single generated document (summary, signature, every receipt
page) that Finance receives. Built by *document processing* (`services/documents.py`)
in a sandboxed or direct *runner*.

**Delivery**: sending a claim's authoritative PDF to its approved recipient. Its state
is derived from its *delivery attempts* (`services/delivery.py`).

**Delivery attempt**: one transmission through a *transport* (local outbox or
MailerSend). Outcomes: `provider_accepted`, `paused`, `delivered`, `failed`, or
`send_unknown` when the provider may have the message.

**Delivery evidence**: a signed webhook event or a reconciliation lookup that updates
an attempt. Evidence never sends anything.

**Operator action**: an audited retry from the admin area or `retry_claim`
(`services/operations.py`).

**Duplicate risk**: an operator resend of a delivery that is not definitely `failed`.
It requires explicit acknowledgment.

**Participant status**: the one status vocabulary shown on the received page, with a
`final` flag (`delivery.participant_status`).

**Operator status**: what operators see of a claim in the admin area and in
`claim_status`: the shown state (the delivery state once a delivery exists, unless
content is being purged), the error code, whether resolution is overdue and whether a
retry carries duplicate risk (`delivery.operator_status`).

**Job**: queued work for one target (`services/queue.py`). A **job kind** (`validate`,
`render`, `send` in `portal/job_kinds.py`) owns its target's transitions. The runner
(`portal/jobs.py`) owns leases and the retry policy.

**Disk reservation**: admission accounting that keeps free private storage above the
floor, headroom and every pending output. A reservation is admitted with an upload,
transferred into an execution *workspace*, released when the outcome is recorded, and
recovered after a crash (`services/capacity.py`).

**Artifact**: a stored private file of a named kind (upload source, normalized upload,
claim PDF, outbox copy, workspace). Storage owns the key layout, integrity checks and
the orphan sweep (`services/storage.py`).

**Scanner**: malware scanning of upload sources, ClamAV in deployments
(`services/scanner.py`). An outage is retried and never counts as clean.

## Seams with two adapters

| Seam | Adapters | Selected by |
|---|---|---|
| Transport | `transport.LocalOutbox`, `mailersend.MailerSendTransport` | `delivery.configured_transport()` |
| Document runner | `SandboxedRunner`, `DirectRunner` (`UnavailableRunner` when misconfigured) | `documents.configured_runner()` |
| Scanner | `ClamAVScanner`, `DisabledScanner` (`MisconfiguredScanner`) | `scanner.configured_scanner()` |
| Disk measurement | `capacity.SystemDisk`, `tests.conftest.FakeDisk` | `capacity.disk` |
