# ProjectTogether Reimbursement Portal

Collect reimbursement details, receipts, and signatures, generate a compressed PDF,
and send it to the configured Moss inbox through MailerSend.

## Run locally

Requires Docker with Compose.

```bash
cp .env.example .env
docker compose build
docker compose up -d database scanner
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_demo
docker compose run --rm web python manage.py createsuperuser
docker compose up -d web worker
```

Open <http://localhost:8000/f/demo/> and use dummy data. Emails are captured
locally. ClamAV's first startup can take several minutes. The admin area is at
<http://localhost:8000/admin/>; locally it accepts the superuser's password.

## Interface styles

Templates use Tailwind classes and the components in `portal/templates/ui/`, ported
from [shadcn-django](https://shadcn-django.com/) and rendered with
`{% component %}` from `portal/templatetags/ui.py`. The stylesheet source is
`portal/assets/ui.css`. The Docker image builds it with the pinned standalone
Tailwind CLI. To run Django outside Docker, build it first; `watch` rebuilds while you
edit templates:

```bash
python scripts/tailwind.py build
python scripts/tailwind.py watch
```

## Admin area

Admins sign in with PT Login (Keycloak) at `/admin/`. Template admins define
templates, the types of forms, including the internal fields each form fills in
(for example KST, KTR or an ID), and maintain the versioned privacy notice. Form
editors create forms from a template, set its internal values and purpose options,
and share the link `/f/<slug>/`. New forms start as drafts and settings save
automatically. **Veröffentlichen** makes a valid form available during its configured
opening period; **Unveröffentlichen** returns it to a draft. Both see the submitted claims of their forms;
template admins can retry processing and delivery.

## Deploy with Coolify

Start one separate PostgreSQL resource in Coolify, then deploy the application as
one resource using `compose.coolify.yaml` and `.env.coolify.example`. It contains
the web service, the single worker, ClamAV and the migration job. All forms are
served from one domain. Follow the [Coolify deployment steps](docs/coolify.md),
including the PT Login client. For upgrades, keep every persistent volume. The
`exclude_from_hc` field is a Coolify extension; use `compose.yaml` locally.

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
