# Domain context

The language this codebase uses for reimbursement intake and delivery, and the module
that owns each concept. Keep it current when a concept gains or changes a home.

## Glossary

**Event**: a configured reimbursement occasion (KST/KTR, approved Finance recipient,
privacy notice, deadline). Every change increments its configuration revision.

**Draft**: one participant's unsubmitted form, bound to a browser session and a secret
token. A draft is *accepting* while it is unsubmitted, its event is open, and its
revision matches the event's (`services/drafts.py`).

**Claim**: the immutable record created when a draft is accepted. Its states are
`received → rendering → ready`, or `processing_failed`, then `purging → purged`.

**Expense**: one row of a claim (merchant, purpose, reference, amount, currency).

**Upload**: a receipt (attached to an expense) or a signature. Uploads are `pending`
until scanned and normalized, then `ready` or `failed`. An upload is *busy* while its
validation runs or an execution workspace holds it (`services/uploads.py`).

**Intake limits**: the file counts, sizes, page limits, amount format, currency
precision and participant messages that the form, its script and server validation
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

**Duplicate risk**: an operator resend of a delivery that is not definitely `failed`.
It requires explicit acknowledgment.

**Participant status**: the one status vocabulary shown on the received page, with a
`final` flag (`delivery.participant_status`).

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
