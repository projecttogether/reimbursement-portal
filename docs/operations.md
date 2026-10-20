# Deployment and operations

## Configure and launch

Use the selected PT application's existing Coolify/TLS proxy. Expose only the web
service. Do not publish PostgreSQL, ClamAV, worker, or storage ports. Keep the
Compose host port on loopback, or remove the port mapping when Coolify routes
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

Create an event JSON from `examples/event.json`, fill its KST/KTR, approved
recipient, Finance contact, privacy notice/version, dates/deadline, and set
`active=true`. Do not use placeholder text or the demo notice with real data.

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm -v /approved/event.json:/tmp/event.json:ro web \
  python manage.py configure_event /tmp/event.json
docker compose up -d web worker
```

Configuration updates increment the event revision. Unsubmitted forms from an
older revision must reload and review again; accepted claims keep their original
configuration snapshot. Run migrations once before replacing the web/worker.
Only one worker process is supported operationally for the MVP; its queue uses
row locks and leases, and prioritizes upload validation over rendering.

The Django deployment check leaves HSTS preload disabled and reports its optional
preload warning. TLS redirection, secure cookies, and HSTS are enabled in production.
Decide preload enrollment with the owner of the selected public domain.

Use completely separate staging databases, files, credentials, and recipients.
Set `DEPLOYMENT_ENV=staging` and a nonempty `TEST_RECIPIENT_ALLOWLIST`. The send
adapter checks that list as well as the approved recipient list.

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
python manage.py claim_status CLAIM_UUID
python manage.py reconcile_deliveries
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
The reconciliation command queries known message IDs without resending; an
unknown send without a message ID requires manual provider lookup.

## Monitoring and maintenance

`/-/healthz` checks only process liveness. `/-/readyz` checks database and writable
storage/capacity. Production probes require `X-Health-Token`. Health checks do not
call MailerSend. The worker updates `worker-heartbeat` in private storage; the
Compose health check permits the bounded document-processing interval.

The worker reclaims expired leases and runs cleanup each minute. Rendering can be
reclaimed; interrupted sends become unknown. It retries only known pre-acceptance
email failures and transient scanner outages, with bounded backoff.

Schedule `reconcile_deliveries` every 15 minutes in the host's scheduler and
monitor its exit status. Monitor queued job age, worker heartbeat, failed jobs,
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
