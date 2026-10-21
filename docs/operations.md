# Deployment and operations

## Configure and launch

For multiple cost centers/domains, follow [Coolify deployment](coolify.md).
Deploy the shared backend once and a separate web resource per domain. Each web
resource uses `EVENT_*` environment variables and synchronizes its event on startup;
no event JSON or terminal configuration is needed. PostgreSQL, files, ClamAV,
the single worker, and one MailerSend webhook are shared on the same Docker host.
PostgreSQL runs as a separate Coolify database resource. Both application Compose
definitions use its complete internal URL and join its Coolify destination network
with Connect To Predefined Network.

Use the selected PT application's existing Coolify/TLS proxy. Expose only the web
and dedicated webhook HTTP services. Do not publish PostgreSQL, ClamAV, worker,
or storage ports. Keep the Compose host port on loopback, or remove the port
mapping when Coolify routes
directly to the container. Do not add a second reverse proxy.

For production set `DJANGO_DEBUG=0`, a random `DJANGO_SECRET_KEY`, production
PostgreSQL credentials/URL, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and
`DEPLOYMENT_ENV=production`. Set `TRUST_PROXY_HEADERS=1` only when the existing
proxy replaces forwarded protocol headers. Configure:

- `MAIL_DELIVERY_MODE=mailersend`, verified `MAIL_FROM_ADDRESS`,
  `MAIL_REPLY_TO_ADDRESS`, `MAILERSEND_API_TOKEN`, and
  `MAILERSEND_WEBHOOK_SIGNING_SECRET`.
- `APPROVED_DELIVERY_RECIPIENTS`, comma-separated approved Moss/Finance inboxes.
- `UPLOAD_SCANNER=clamav`, its private host/port, and `HEALTHCHECK_TOKEN`.
- Persistent encrypted database/private storage volumes and an independent,
  encrypted off-host backup destination. Backup keys remain outside Git.

For the original combined deployment, create an event JSON from
`examples/event.json`, fill its KST/KTR, approved recipient, Finance contact,
privacy notice/version, dates/deadline, and set
`active=true`. Do not use placeholder text or the demo notice with real data.

```bash
docker compose stop web worker
docker compose run --rm web python manage.py migrate
docker compose run --rm -v /approved/event.json:/tmp/event.json:ro web \
  python manage.py configure_event /tmp/event.json
docker compose up -d web worker
```

Configuration changes increment the event revision; unchanged restarts do not.
Unsubmitted forms from an older revision must reload and review again; accepted
claims keep their original
configuration snapshot. Stop every web resource and the shared worker before
migrations, then replace all with the new image. Old workers must not create
reservations during this upgrade.
Only one worker process is supported operationally; its queue uses row locks and
leases and acquires ready jobs in FIFO order across validation, rendering and
sending. Later uploads cannot indefinitely overtake accepted claims.

The Django deployment check leaves HSTS preload disabled and suppresses only its
optional preload warning. TLS redirection, secure cookies, and HSTS are enabled in production.
Decide preload enrollment with the owner of the selected public domain.

The supplied image requires Linux x86_64/aarch64, Landlock ABI 3 or newer and
libseccomp. Startup and readiness run a real confinement probe. Document children
can read only their selected inputs and trusted runtime/font files, can write only
their own workspace, and cannot open network sockets. Keep the private 700 MB
tmpfs mount at `PRIVATE_STORAGE_ROOT/work` on both web and worker. Startup rejects
a missing, shared or unbounded workspace in production. This mount is separate
from persistent source/PDF storage; publishing copies files atomically between them.
Workspace reservations charge the tmpfs allowance there and reserve only the
bounded normalized/final output on persistent storage. Local setups sharing one
filesystem reserve both against that filesystem.
Keep the supplied web/worker limit of 64 processes/threads per container. The
syscall filter also denies signaling and changing another process's resource or
scheduling limits, including pidfd and queued-signal variants.

Use completely separate staging databases, files, credentials, and recipients.
Set `DEPLOYMENT_ENV=staging` and a nonempty `TEST_RECIPIENT_ALLOWLIST`. The send
adapter checks that list as well as the approved recipient list.

## Draft admission

New drafts share a PostgreSQL-backed budget across sessions and web processes.
`MAX_DRAFTS_PER_NETWORK` defaults to 300 creations per 24 hours per IPv4 address or
IPv6 /64, allowing the expected 250 participants to share a venue connection.
Existing drafts remain accessible after the creation limit is reached. No raw IP
address is stored in the counter, and inactive counter keys expire after two days.
Set `TRUSTED_PROXY_CIDRS` to the exact reverse-proxy networks if the application
receives proxied requests. The proxy must replace or correctly append
`X-Forwarded-For`; the app walks that chain from the trusted connection to the first
untrusted address. With no trusted proxies configured, it uses `REMOTE_ADDR`.
Configure the edge's connection/request limits as well and monitor 429/503 rates.

Upload admission also charges cumulative processing work: 200 uploads / 150 MB per
draft and 60,000 uploads / 45 GB per network per day, with 3,000 uploads per network
per minute. Deleting or replacing a file does not refund those counters. A pending
upload reserves 10 MB for normalization; each draft permits 100 MB of derived and
reserved data. Normalized images are resized and capped at 10 MB of serialized
output. A global database lock protects admission against concurrent processes,
including the configured free-space floor, request-spooling headroom and active
execution reservations. At most 1,000 validations may be queued/running. Tune the
documented environment limits against measured deployment capacity before launch.

## MailerSend and handoff

Create a version 2 webhook for sent, delivered, deferred, soft/hard bounced, and
suppressed events at `/webhooks/mailersend/`. Check which events the account plan
supports. The endpoint verifies raw-body signatures, handles the signed setup
ping, and records minimal deduplicated events. Opaque attempt tags can correlate
a callback arriving before the send response commits. Disable open/click/content
tracking, as the provider adapter requests.

A locally accepted claim is not a payment approval. API acceptance is not email
delivery, and recipient-server delivery is not proof of completed Moss processing.
The merged PDF is the authoritative document in the established Finance workflow.
Finance must accept the selected handoff/deletion policy before setting
`CONTENT_PURGE_ENABLED=1`. It defaults to `0`; abandoned uploads and orphan
temporary files are still cleaned. Set `SUCCESS_RETENTION_DAYS` to the approved
period, initially proposed as seven days. An already started purge always resumes.

Run a complete dummy claim through the approved MailerSend/Moss route before
launch. Check all pages, KST/KTR, declaration, signature, amounts, and compressed
readability. The service compresses the complete PDF in bounded passes down to
72 DPI / JPEG quality 50. If the complete document still exceeds the configured
cap, it remains a processing exception. No page is silently removed and no partial
attachment is sent. Inspect the retained sources and resolve with Finance through
an approved private process.

## Recovery commands

Commands run through existing authorized host access. There is no staff web login.
Use operational reasons without participant names, account details, or receipt text.

```bash
python manage.py claim_status
python manage.py claim_status --state processing_failed
python manage.py claim_status CLAIM_UUID
python manage.py reconcile_deliveries --batch-size 100
python manage.py verify_storage
python manage.py retry_claim CLAIM_UUID --reason 'Resolved the sender configuration'
python manage.py retry_claim CLAIM_UUID --reason 'Provider activity checked; intentional resend' \
  --acknowledge-duplicate-risk
python manage.py purge_expired
python manage.py purge_expired --execute
```

`retry_claim` records the host actor and reason. It retries failed renders only
before a PDF has become ready. Delivery retries reuse the existing immutable PDF
and destination. An ambiguous send, paused/accepted send, or intentional resend
requires explicit duplicate-risk acknowledgment. A new claim is required for
changed participant data; Finance handles replacement and late-submission policy.
If a closed event needs another claim, coordinate its reopening through versioned
configuration. The MVP has no automatic replacement or payment cancellation.

An interrupted send or a timeout after transmission can mean MailerSend accepted
the message. These cases become `send_unknown` and do not auto-retry. Inspect
provider activity first. Signed events with an attempt tag may resolve the case.
A failure before transmission, such as a missing or corrupt PDF or a blocked
recipient, is a definite `failed` delivery and can be retried without
acknowledgment once its cause is fixed.
The reconciliation command queries known message IDs without resending; an
unknown send without a message ID requires manual provider lookup. A `queued`
provider status resolves `send_unknown` to accepted.
Reconciliation treats MailerSend's `rejected` email status as a terminal failure,
using suppression or hard-bounce details when present. It retains the PDF for
resolution and never resends automatically.

## Monitoring and maintenance

`/-/healthz` checks only process liveness. `/-/readyz` checks database, writable
storage/capacity, document confinement, the bounded workspace, scanner PING,
worker heartbeat and oldest ready-job age (default maximum 900 seconds).
Production probes require `X-Health-Token`. Health checks do not call MailerSend.
The worker updates `worker-heartbeat` in private storage; heartbeat checks permit
the bounded document-processing interval.

HTTP responses include a generated `X-Request-ID`. Access logs contain this ID,
method, status and duration. Application error stacks retain exception type and
code locations while excluding exception values, source text, bodies, paths,
headers and local variables. Use request IDs and operational references to
investigate failures without putting financial content in logs.

The worker reclaims expired leases and runs cleanup each minute. Reservations older
than a job lease are reclaimed when their execution lock is free, including after
a restart clears tmpfs. Both supervisor and parser hold the lock, so a stale job
cannot cause cleanup to remove a live parser's workspace. An interrupted
validation keeps its admitted output reservation while awaiting another attempt.
Rendering can be
reclaimed; interrupted sends become unknown. It retries only known pre-acceptance
email failures and transient scanner outages, with bounded backoff.

Queue age measures waiting time since `run_after` for runnable/running jobs.
Scheduled backoffs do not fail readiness; a newly due retry gets a fresh waiting
interval. `claim_status` uses the same measure. Transient sandbox probe failures
are retried by the next readiness request; successful probes remain cached.

Schedule `reconcile_deliveries` every 15 minutes in the host's scheduler and
monitor its exit status. It rotates through bounded batches, including failed
provider lookups, and exits nonzero on provider failures; unmatched webhook events
also rotate. `claim_status` streams all matches and supports state filters.
Monitor queued job age, worker heartbeat, failed jobs,
`send_unknown`/paused/failed deliveries, scanner availability, free disk, and purge
failures. Use the independent PT monitoring channel, so a MailerSend outage cannot
suppress its own alert. `claim_status` emits redacted operational state for this.
Name the technical owner and Finance contact before launch.

Drafts expire after 24 hours. The proposed successful-claim retention is seven
days after recipient-server delivery. Failed/ambiguous claims have a 30-day
resolution target and remain retained if unresolved; logs report approaching and
overdue cases. The owner must resolve them or record an approved extension. These
periods need Finance/Legal approval and are not legal retention determinations.
After content purge, minimal reference/time/status records expire after 30 days.
MailerSend and Moss copies follow their separately approved retention policies.

## Backup and restore

Back up PostgreSQL and private source/PDF files together, encrypted and off-host.
Do not back up only the database. Record the backup interval, expiry, key access,
recovery time target, and accepted loss window. The current single-host acceptance
path cannot promise survival of total host loss since the last backup; zero-loss
acceptance requires off-host file durability and matching database durability.

After restoring, leave the public web service stopped. Run overdue purges, apply
any separately recorded exceptional deletions, and inspect interrupted deliveries
before restarting processing. Never automatically resend every restored job.
Test a restore using dummy data and verify database/file consistency, unknown-send
handling, and deletion. Keep exceptional deletion evidence outside the rollback
scope so an old backup cannot silently reactivate deleted financial data.
Run `python manage.py verify_storage` before restarting the restored application;
it checks recorded source/final PDF hashes, sizes and normalized-file presence
without printing participant content. `./scripts/capacity.sh 250` includes a
separate synthetic restore/deletion exercise; see `docs/production-readiness.md`
for its scope and the remaining staging and owner evidence.
