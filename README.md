# PT Reimbursement Portal

German reimbursement intake for external ProjectTogether participants. The portal
collects the existing reimbursement form's fields and private receipts, builds one
compressed authoritative PDF, and sends it to the configured Moss inbox through
MailerSend. There is no staff login or public document download.

## Run locally with Docker

```bash
cp .env.example .env
docker compose build
docker compose up -d database scanner
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py configure_event --demo
docker compose up -d web worker
```

Open <http://localhost:8000/forms/demo/>. ClamAV's first startup downloads its
signature database and can take several minutes. Local settings capture email in
the private volume's `outbox/` directory and never contact MailerSend.

Use dummy data only. The seeded event includes a development privacy notice and
`example.invalid` email addresses. Upload limits are 10 MB per receipt, 50 MB per
claim, and 1 MB for a signature. PDF compression targets at most 10 MB and retains
all receipt pages in order.

## Python development

Install Python 3.13 and `uv`. WeasyPrint needs Pango and local fonts; the Docker
image supplies these dependencies. Django itself can run locally without them,
but the document worker and document tests need the image or equivalent libraries.

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py configure_event --demo
uv run python manage.py runserver
# In a second terminal with the PDF libraries available:
uv run python manage.py run_jobs
```

Local Python defaults to SQLite, private `var/private/` storage, disabled malware
scanning, and local email capture. Production requires PostgreSQL, ClamAV, and
MailerSend. Do not expose the local development configuration to real participants.

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
docker build --target test -t reimbursement-test .
docker run --rm reimbursement-test pytest -q
```

CI also runs the suite against PostgreSQL, including racing-submission tests, and
builds the production image. Browser verification steps are in
[docs/browser-checks.md](docs/browser-checks.md).

See [plan.md](plan.md), the [source form mapping](docs/form-contract.md), and
[operations](docs/operations.md) for the MVP contract and deployment requirements.
