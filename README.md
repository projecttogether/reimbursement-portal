# Reimbursement Portal

ProjectTogether's open-source reimbursement app, for any organization to run on its
own server. Collect expense claims, receipts and signatures, assemble one PDF, and send
it to an approved accounting inbox. Participants receive a separate confirmation without attachments. Participants
use it in German or English; the admin area is always in English.

The app includes configurable form templates, receipt and mileage expenses, private
uploads, malware scanning, PDF compression, role-based administration, delivery
tracking, retries, retention cleanup, and health checks.

## Run locally

Requires Docker with Compose. Local delivery captures private `.eml` files and
sends no external email. `compose.yaml` is the production setup; the
`COMPOSE_FILE` line in `.env` makes these commands use `compose.dev.yaml`.

```bash
cp .env.example .env
docker compose build
docker compose up -d database scanner
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_demo
docker compose run --rm web python manage.py createsuperuser
docker compose up -d web worker
```

Open <http://localhost:8000/f/demo/>. The admin area is at
<http://localhost:8000/admin/>. The scanner's initial startup may take several minutes.
Use synthetic data only. Database passwords in the examples are local placeholders.

For development outside Docker, install Python 3.13, uv, the system libraries listed
in `Dockerfile`, and run:

```bash
uv sync --locked
uv run python scripts/tailwind.py build
export DJANGO_DEBUG=1 DEPLOYMENT_ENV=development
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py createsuperuser
uv run python manage.py runserver
# In a second terminal with the same environment:
uv run python manage.py run_jobs
```

This development mode uses SQLite, skips malware scanning, and captures email
locally. Document confinement requires Linux Landlock ABI 3+ and libseccomp2.
A local-only fallback is available with `DOCUMENT_SANDBOX_ENABLED=0`.

## Configure a deployment

Each operator supplies their own identity and infrastructure settings. No credentials,
production data, or external account identifiers are included. Configure:

- `PORTAL_OPERATOR`, `PORTAL_LEGAL_URL` for the footer of public pages.
- `KEYCLOAK_BASE_URL`, `KEYCLOAK_REALM`, client credentials and role groups for admin sign-in.
- `APPROVED_DELIVERY_RECIPIENTS`, the Finance inboxes forms may send to.
- PostgreSQL, private storage, malware scanner, TLS proxy and health check settings.
- Optionally `SENTRY_DSN` for error reports without claim content.

In the admin area under Admin > General, choose the app language (German or
English), timezone, date format and number format (European 12,50 by default, or US
12.50). The app language applies to public pages, emails to participants and Finance,
and the claim PDF. Under Admin > Email, choose how the app sends email: MailerSend with
an API token, or an SMTP server. Set the sender address there, and turn the
participant confirmation off or write its subject and text; a test email checks the
connection. Forms take no claims until email is set up. The environment can supply the
MailerSend token, its webhook signing secret and the addresses instead
(`MAILERSEND_API_TOKEN`, `MAILERSEND_WEBHOOK_SIGNING_SECRET`, `MAIL_FROM_ADDRESS`,
`MAIL_REPLY_TO_ADDRESS`); a value set there wins. Under Admin > Appearance, optionally
set an accent color as a hex code and upload a favicon; both apply to public pages
and the admin area. Without an accent color, the app uses ProjectTogether's colors.
Replace the initial privacy setup notice in the admin area before opening forms.
Review the declaration and retention periods for your deployment. Texts written in the
admin area, such as the privacy notice, declarations and form descriptions, are shown
as written, so write them in the app language. Payment timing is left to the
operator's support process.

Production requires PostgreSQL, Keycloak, MailerSend or an SMTP server, ClamAV, a bounded private
document workspace, and worker heartbeat checks. Password sign-in is available
only in development. See [deployment](docs/deployment.md) on a Linux server (Debian,
Ubuntu) and [operations](docs/operations.md).

## Manage forms

Template admins manage templates, internal accounting fields, privacy notices and
all claims. Form editors manage their own forms and see their submissions. New
forms start as drafts and save automatically. Publish a valid form, then share
`/f/<slug>/`. A primary form can serve as the start page. Forms can open and close
on a schedule. A form takes claims while it is published, within its opening times,
its internal values fit its template, its receiving inbox is one of
`APPROVED_DELIVERY_RECIPIENTS` and a privacy notice exists. The start page redirects
only then, and the forms list's Open now column shows it, naming what keeps an open
form from taking claims. Claims retain the form configuration and consent version used at
submission. Archive forms and claims you are done with to keep the lists short; the
Status filter's Archived option lists them again.

## Development checks

```bash
uv run ruff check .
uv run ruff format --check .
DJANGO_DEBUG=1 uv run python manage.py check
DJANGO_DEBUG=1 uv run python manage.py makemigrations --check --dry-run
uv run pytest -q
node --test tests/js/*.test.cjs
python3 scripts/check_public_repository.py
```

The [CI workflow](.github/workflows/ci.yaml) runs the server suite against disposable
PostgreSQL and builds the production image. A second job starts that image with a
worker and runs the participant smoke test in Chromium and the setup check.
`compose.ci.yaml` contains only inert test settings. [Browser checks](docs/browser-checks.md)
describes the browser scripts and how to run that job locally.

Interface templates use local components and inline geometric icons. Tailwind
builds the stylesheet from `portal/assets/ui.css`. No remote fonts or client-side
CDN resources are required. Rebuild styles with `python scripts/tailwind.py build`,
or use `watch` while editing templates.

The admin area has a frame of its own (`admin/base.html`, `frame.css` and `frame.js`):
the sidebar with the search and the person's menu, the header with the breadcrumbs,
and keyboard shortcuts. ⌘K or Ctrl+K and "/" search, "G then O" opens Claims and "G
then F" Forms, Ctrl+/ lists the shortcuts and ⌘B or Ctrl+B hides the sidebar. The page
header, the sign-in page, the person's Settings page and the components in `ui.css`
(buttons, badges, cards, alerts, fields) follow shadcn/ui's class names. Appearance
(light, dark or the device's setting) and the single-letter keys are kept in the
browser.

The colors are ProjectTogether's (`portal/assets/tokens.css`, from PT's style sheet):
PT Sky for buttons and focus, PT Ocean for text, and a darker shade of PT Sky for links,
so that they reach a contrast of 4.5. The app icon is the receipt of
[Lucide](https://lucide.dev/icons/receipt-euro) on PT Sky; PT's logo stands in the
sidebar, on the sign-in page and on the About card. Dark mode is the class `dark` on
`<html>`, which `portal/static/portal/theme.js` sets before the page shows.

See the [form contract](docs/form-contract.md) for validation and document behavior,
and [release checks](docs/production-readiness.md) for deployment verification.

## Builds and deploys

Every commit on `main` that passes CI is built as
`ghcr.io/projecttogether/reimbursement-portal:sha-<commit>` (and `:latest`) and
deployed to ProjectTogether's server by the Deploy workflow; see
[builds and deploys](docs/releases.md).

## License

Copyright 2026 ProjectTogether gGmbH

Licensed under the [Apache License 2.0](LICENSE).
