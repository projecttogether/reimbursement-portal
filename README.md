# PT Reimbursement Portal

Collect reimbursement details, receipts, and signatures, generate a compressed PDF,
and send it to the configured Moss inbox through MailerSend.

## Run locally

Requires Docker with Compose.

```bash
cp .env.example .env
docker compose build
docker compose up -d database scanner
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py configure_event --demo
docker compose up -d web worker
```

Open <http://localhost:8000/forms/demo/> and use dummy data. Emails are captured
locally. ClamAV's first startup can take several minutes.

## Deploy with Coolify

Start one separate PostgreSQL resource in Coolify, then deploy the shared backend
using `compose.coolify.yaml` and `.env.coolify.example`. The backend owns ClamAV,
the single worker, migrations, and the MailerSend webhook. Deploy a separate web
resource per domain using `compose.coolify.web.yaml` and `.env.coolify.web.example`,
with each cost center configured through `EVENT_*` environment variables. Web
startup creates or updates its event automatically.

All application resources share the database's complete internal connection URL
and join its Coolify destination network with Connect To Predefined Network.
They also share one private storage volume and application network on the same
host. Each domain serves only its configured event. The backend's single
signed MailerSend webhook updates deliveries across all events.

Follow the [Coolify deployment steps](docs/coolify.md). For upgrades, stop all web
resources and the shared worker before migrations, and keep every persistent
volume. The backend's `exclude_from_hc` field is a Coolify extension; use
`compose.yaml` for the original local Docker Compose setup.

## Local CI

```bash
./scripts/ci.sh
```

Runs lint, formatting, Django and migration checks, PostgreSQL tests, and a
production deployment check and image build. CI runs locally only, using a temporary database.

```bash
./scripts/capacity.sh 250
```

Runs a synthetic intake burst in disposable PostgreSQL/web/worker containers,
checks a database/files restore and deletion, and removes their volumes. It sends
no external mail. Requires a Linux host with Landlock ABI 3 or newer. See the
[release checklist](docs/production-readiness.md) for scope, limits and launch evidence.

See [operations](docs/operations.md), [form mapping](docs/form-contract.md), and
[browser checks](docs/browser-checks.md).
