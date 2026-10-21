# Coolify deployment

Deploy one shared backend, then one web resource for each public domain and
Kostenstelle. Use the Git-based Docker Compose build pack for both resource
types, with Base Directory `/` and Raw Compose deployment disabled.

The backend uses `compose.coolify.yaml`. It contains PostgreSQL, ClamAV, one
worker, the migration job, and a dedicated `webhook` HTTP service. Each web resource
uses `compose.coolify.web.yaml` and contains only a `web` HTTP service.
The Dockerfile builds the same runtime image for every application process.

## 1. Prepare the host

Use one Linux Docker host with Landlock ABI 3 or newer. Start with enough memory
for the backend limits, approximately 5 GB, plus 512 MB per web resource, build
memory, and the host/Coolify processes. The private volume must be on encrypted
storage with at least the configured free-space floor, initially 1 GB.

On that host, create the shared Docker network and private file volume once:

```bash
docker network create pt-reimbursement-shared
docker volume create pt-reimbursement-private
```

These names are the defaults for `SHARED_NETWORK` and `PRIVATE_DATA_VOLUME` in both
Compose definitions. They are external resources so Coolify preserves their names
across separate applications. PostgreSQL and scanner signature volumes remain
owned by the backend resource. Do not create another backend for each cost center.

All resources must use this same Docker host. A named Docker volume or bridge
network does not share data or connections across hosts. Use separate names,
databases, credentials, and recipients for staging.

## 2. Deploy the shared backend

1. Push the deployment files and application code to the selected Git branch.
2. Create a Coolify Git-based application. Select Docker Compose and set Compose
   Location to `compose.coolify.yaml`. Leave custom build/start commands empty.
3. Paste `.env.coolify.example` into Environment Variables. Replace every
   `REPLACE_WITH` value. Keep secrets runtime-only.
4. Set `POSTGRES_PASSWORD` to a random hexadecimal password. Use that same password
   inside `DATABASE_URL`, whose hostname is the stable shared network alias
   `pt-reimbursement-database`. The default database/user are both `reimbursement`.
5. Generate a `DJANGO_SECRET_KEY` of at least 50 characters and a health token.
   Running `openssl rand -hex 32` separately for each produces suitable values.
6. Set `ALLOWED_HOSTS` to the webhook hostname plus `localhost,127.0.0.1`. Set
   `TRUSTED_PROXY_CIDRS` to the actual proxy networks, not a universal CIDR.
7. Set the shared MailerSend API token, verified sender, reply-to address, and
   webhook signing secret. `APPROVED_DELIVERY_RECIPIENTS` must include every
   approved destination used by any cost center.
8. Assign only `webhook` a public domain, for example
   `https://reimbursement-hooks.example.org:8000`. Point its DNS to the server.
9. Deploy. `migrate` must exit successfully; database, scanner, worker and webhook
   must become healthy. ClamAV's initial signature download can take several minutes.

Static files are collected during the image build in a clean development
environment. This step needs no database connection or deployment secrets and
ignores application variables injected by Coolify as build arguments. The running
containers still require all production settings above.

When using project shared variables, store the complete PostgreSQL URL as the
project's `DATABASE_URL`, then reference it on each application resource with
`DATABASE_URL={{project.DATABASE_URL}}`. Its resolved value must begin with
`postgresql://` or `postgres://`; the URL in `.env.coolify.example` uses the database
included in this backend. An `UnknownSchemeError` indicates an invalid URL value,
including an empty value or an unresolved shared-variable reference. Verify the
resolved value privately in Coolify; do not paste database credentials into logs.

If there is no MailerSend webhook yet, use a temporary random signing secret for
the initial backend deployment. Create a version 2 webhook pointing to
`https://reimbursement-hooks.example.org/webhooks/mailersend/`, then replace the
temporary value with MailerSend's signing secret and redeploy. Enable sent,
delivered, deferred, soft/hard bounce and suppressed events where supported.
Disable open, click and content tracking. No event needs its own MailerSend token
or webhook. The backend matches callbacks using delivery/attempt identifiers in
the shared database, including deliveries from other domains.

The proxy joins Coolify's resource network for HTTP routing. Application/database
traffic uses the explicitly shared network. Check the networks actually shared
by the proxy and HTTP container before setting trusted CIDRs. For a network named
`coolify`, inspect the subnet with:

```bash
docker network inspect coolify --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}'
```

## 3. Deploy a web resource for each cost center

1. Create another Git-based Docker Compose application from the same repository
   and release, on the same host. Set Compose Location to `compose.coolify.web.yaml`.
2. Paste `.env.coolify.web.example` into Environment Variables.
3. Copy the exact shared `DATABASE_URL`, `DJANGO_SECRET_KEY`, `SHARED_NETWORK`,
   `PRIVATE_DATA_VOLUME`, and `CLAMAV_HOST`. Coolify project/environment shared
   variables can hold values used by all resources. A shared Django secret keeps
   session hashes and network admission budgets consistent across processes;
   browser session cookies remain host-only.
4. Set `ALLOWED_HOSTS` to this web resource's hostname plus `localhost,127.0.0.1`.
   Set `CSRF_TRUSTED_ORIGINS` to its HTTPS origin and configure its actual trusted
   proxy CIDRs. Do not include the other cost centers' domains.
5. Set the event variables shown below. Use a unique, stable `EVENT_SLUG` for each
   cost center. Keep `EVENT_ACTIVE=0` until the configuration is ready to test.
6. Assign only `web` its public domain, for example
   `https://workshop-a.example.org:8000`, and configure DNS.
7. Deploy. Web startup validates the event variables and synchronizes that event
   into PostgreSQL before Gunicorn starts. Missing/invalid settings stop startup.
8. Set `EVENT_ACTIVE=1` and redeploy when the approved settings and webhook are
   ready. Complete a synthetic claim privately before distributing the domain.

Required event values:

```dotenv
EVENT_SLUG=workshop-a
EVENT_NAME=ProjectTogether Workshop A
EVENT_COST_CENTER=KST-100
EVENT_RECIPIENT=approved-finance-inbox@example.org
EVENT_SUPPORT_EMAIL=finance@example.org
EVENT_PRIVACY_NOTICE=The approved privacy notice
EVENT_PRIVACY_VERSION=approved-v1
```

`EVENT_COST_OBJECT` supplies the optional Kostentraeger. Optional settings include
`EVENT_FINANCE_NOTE`, `EVENT_INSTRUCTIONS`, `EVENT_ALLOWED_CURRENCIES`,
`EVENT_TRAVEL_REQUIRED`, `EVENT_TRIP_START`, `EVENT_TRIP_END`, `EVENT_OPENS_AT`, and
`EVENT_CLOSES_AT`. Dates use `YYYY-MM-DD`. Opening/closing timestamps require an
explicit timezone, such as `2026-11-01T09:00:00+01:00`. Boolean values accept
`0`, `1`, `false`, and `true`. Currencies are comma-separated ISO codes.
Use Coolify's multiline editor for privacy notices with paragraphs.

`EVENT_RECIPIENT` must be approved by both this web resource and the shared worker.
Web resources do not require MailerSend API or webhook secrets. All sending uses
the shared worker's sender, reply-to address, token, and retention configuration.

Visit the domain root to reach that event. The web resource rejects other event
slugs on forms, uploads, submissions, and claim-status pages, even if a valid
session from another domain is replayed. Shared webhook/worker roles cannot serve
intake pages; web resources do not expose the MailerSend webhook.

Duplicate this web resource for another cost center, changing its domain, origin,
event slug and event settings. Do not duplicate the backend. Keep aggregate limits
consistent across resources. Each web resource runs two Gunicorn workers, so set
`TOTAL_WEB_WORKERS` in all resources to twice the number of web resources. This
reserves enough upload-spooling headroom on their shared file storage.

## 4. Verify and operate

Complete a synthetic claim on each domain. Check its Kostenstelle/Kostentraeger,
all PDF pages, signature, correct recipient, and delivered status through the one
shared webhook. Check that another event's form URL returns 404 on that domain.
Back up PostgreSQL and private files together, encrypted and off-host. A Docker
volume does not provide encryption or a backup by itself.

Schedule `python manage.py reconcile_deliveries --batch-size 100` every 15 minutes
in the shared worker. Worker maintenance already performs periodic cleanup.
Enable `CONTENT_PURGE_ENABLED` only after the handoff and retention policy have
been approved; its settings belong on the shared backend.

Changing event environment variables requires a web redeployment, so its startup
command runs again. An unchanged restart does not advance the event revision.
A changed configuration advances it once; existing drafts must reload and review
the new configuration, while accepted claims retain their original snapshot.
Keep slugs stable: changing a slug creates another event and does not move earlier
claims or automatically close the old event. Close the old event through its
existing web configuration before replacing its resource if that is intended.

For schema/code upgrades, stop every web resource and the shared backend before
deploying the new backend. Keep all volumes. Once backend migrations succeed,
deploy all web resources at the same release. Coordinate rollback with database
compatibility and do not run old workers through a migration.

The JSON/`--demo` event command and combined `compose.yaml` setup remain available
for the original local workflow. They are unnecessary for the Coolify domain
resources. See [operations](operations.md) for recovery, monitoring, and restore.

References: [Coolify Compose deployment](https://coolify.io/docs/applications/builds/docker-compose),
[shared storage names](https://coolify.io/docs/core/persistent-storage/storage-mounts/volume-mounts),
and [Coolify networking](https://coolify.io/docs/core/networking-in-coolify).
