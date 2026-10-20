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

## Local CI

```bash
./scripts/ci.sh
```

Runs lint, formatting, Django and migration checks, PostgreSQL tests, and a
production image build. CI runs locally only, using a temporary database.

See [operations](docs/operations.md), [form mapping](docs/form-contract.md), and
[browser checks](docs/browser-checks.md).
