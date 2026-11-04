# Coolify deployment

The application is one Coolify resource built from `compose.coolify.yaml`: the
`web` HTTP service, one `worker`, ClamAV (`scanner`) and a one-time `migrate` job.
All of them use the same image. PostgreSQL is a separate Coolify database resource.
Every form is served from the one domain `reimbursement.projecttogether.org` at `/f/<slug>/`; admins create forms in
the admin area at `/admin/` instead of deploying anything per cost center.

## 1. Prepare the host

Use one Linux Docker host with Landlock ABI 3 or newer. Plan for approximately
5 GB of memory for the application limits (web 512 MB, worker 2 GB, ClamAV 2 GB),
plus the database, builds and the host/Coolify processes. The private volume must
be on encrypted storage with at least the configured free-space floor, initially
1 GB. Coolify creates the private file and scanner volumes with the resource.

## 2. Start the separate PostgreSQL resource

1. Open the existing PostgreSQL resource, or create one database resource in
   Coolify on the same host and Coolify destination as the application.
2. Start PostgreSQL and wait until it is healthy. Keep it private; the application
   does not need a public database port.
3. In the database's General page, copy **Postgres URL (internal)**. It becomes the
   application's `DATABASE_URL`:

```dotenv
DATABASE_URL=postgresql://USER:URL_ENCODED_PASSWORD@COOLIFY_DATABASE_HOST:5432/DATABASE_NAME
```

Use the generated URL; do not paste it into logs or chat. An `UnknownSchemeError`
indicates an empty value or an unresolved shared-variable reference.

## 3. Create the PT Login client

Create the client by hand in the Keycloak admin console of PT Login
(`https://auth.projecttogether.net`, realm `projecttogether`), following the
conventions in pt-infra (`hetzner/pt-iam/README.md`):

| Setting | Value |
| --- | --- |
| Client ID | e.g. `reimbursement-portal` |
| Client authentication | On (this adds the Credentials tab with the secret) |
| Standard flow | On; direct access grants and service accounts Off |
| Require PKCE | On (S256; the portal always sends a code challenge) |
| Valid redirect URI | `https://reimbursement.projecttogether.org/oidc/callback/` |
| Web origin | `https://reimbursement.projecttogether.org` (no trailing slash) |
| Valid post logout redirect URI | `https://reimbursement.projecttogether.org/*` |

Add a **Group Membership** mapper to the client's dedicated scope: token claim name
`groups`, full group path On, added to the ID token and userinfo. Do not request a
`groups` scope. Create the two groups below (or choose others and set the variables):

| Group (full path) | Role in the portal |
| --- | --- |
| `/projecttogether/tools/reimbursement-portal-admins` (`KEYCLOAK_ADMIN_GROUP`) | Template admins: templates, privacy notice, all forms and claims, retries |
| `/projecttogether/tools/reimbursement-portal` (`KEYCLOAK_EDITOR_GROUP`) | Form editors: create forms from templates, see their forms' claims |

Sign-in requires a verified email address and one of the groups. Roles are taken
from the groups at every sign-in; an admin session expires eight hours after
successful authentication, even when the session changes. Sessions issued with the
older inactivity timeout require a fresh sign-in after upgrading.
Copy the client ID into `OIDC_CLIENT_ID`. The secret is in the client's
**Credentials** tab (Client Authenticator "Client Id and Secret"); copy it into
`OIDC_CLIENT_SECRET`. Regenerating it there invalidates the old value.

## 4. Deploy the application

1. Create a Coolify Git-based application from this repository with the Docker
   Compose build pack, Base Directory `/` and Compose Location `compose.coolify.yaml`.
   Leave custom build/start commands empty. Under Advanced > Docker compose, enable
   **Connect To Predefined Network** on PostgreSQL's destination, so all containers,
   including the migration job, reach the internal database hostname.
2. Paste `.env.coolify.example` into Environment Variables and replace every
   `REPLACE_WITH` value. Keep secrets runtime-only.
3. Generate `DJANGO_SECRET_KEY` (at least 50 characters) and `HEALTHCHECK_TOKEN`,
   e.g. with `openssl rand -hex 32` each.
4. Set `ALLOWED_HOSTS` to `reimbursement.projecttogether.org,localhost,127.0.0.1`,
   `CSRF_TRUSTED_ORIGINS` to `https://reimbursement.projecttogether.org`, and `TRUSTED_PROXY_CIDRS` to the
   actual proxy networks, never a universal CIDR. For a network named `coolify`:
   `docker network inspect coolify --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}'`.
5. Set the MailerSend token and webhook signing secret. Every form sends from
   `no-reply@projecttogether.org` with reply-to `buchhaltung@projecttogether.org`;
   both are fixed in the code, so `projecttogether.org` must be a verified
   MailerSend domain. `APPROVED_DELIVERY_RECIPIENTS` lists every Moss/Finance inbox that forms
   may send to; admins choose one of them per form.
6. Assign only `web` the public domain `https://reimbursement.projecttogether.org:8000` and point DNS to the
   server.
7. Deploy after PostgreSQL is healthy. `migrate` must exit successfully; web, worker
   and scanner must become healthy. ClamAV's first signature download can take
   several minutes. The first migration creates the initial privacy notice and the
   template "Externe Erstattung" with the fields KST, KTR and ID.

Static files are collected during the image build with development defaults; this
needs no database or secrets. The running containers require all settings above.

If there is no MailerSend webhook yet, deploy with a temporary random signing
secret. Then create a version 2 webhook for `https://reimbursement.projecttogether.org/webhooks/mailersend/`,
set its signing secret and redeploy. Enable sent, delivered, deferred, soft/hard
bounce and suppressed events where supported; disable open, click and content
tracking.

## 5. Verify and operate

1. Sign in at `https://reimbursement.projecttogether.org/admin/` with PT Login. Review the privacy notice
   (Datenschutzhinweise) and the template; adjust or add templates if needed.
2. Create a form: choose the template, set the internal values (KST, KTR, ID),
   the purpose options, the recipient and the support contact. New forms are
   inactive; their owners can preview the link before activating them.
3. Complete a synthetic claim. Check the internal values, all PDF pages, the
   signature, the recipient and the delivered status shown in the admin area.
4. On the server, check that the web container's HTTPS router lists the
   `reimbursement-portal-buffering` middleware in its
   `traefik.http.routers.https-….middlewares` label (`docker inspect` shows the
   container's labels). The two synchronous web workers rely
   on Traefik receiving each request completely before forwarding it; without the
   buffer, two slowly sent requests can make the portal unresponsive.

Back up PostgreSQL and the private files together, encrypted and off-host. A Docker
volume provides neither encryption nor a backup. Schedule
`python manage.py reconcile_deliveries --batch-size 100` every 15 minutes in the
worker container. Enable `CONTENT_PURGE_ENABLED` once Finance has accepted the
handoff; the privacy notice states the 7-day PDF and 30-day content periods.

Form, template and privacy notice changes take effect immediately. Open drafts of
affected forms must reload and be reviewed again; accepted claims keep their
snapshot. Keep a form's slug stable once its link has been sent.

Moving from the earlier event-based release (one web resource per cost center)
requires a fresh start: its migration history was replaced. Remove the old backend
and web resources, drop and recreate the database, and start with empty private
storage. Against an old database the migration job stops with an explicit error
and changes nothing.

For schema/code upgrades, Coolify redeploys the whole resource: the migration job
runs before web and worker start. Keep all volumes. Coordinate rollbacks with
database compatibility and never run an old worker against a newer schema.

The combined `compose.yaml` remains the local development setup.

References: [Coolify Compose deployment](https://coolify.io/docs/applications/builds/docker-compose),
[standalone PostgreSQL](https://coolify.io/docs/databases/postgresql)
and [Coolify networking](https://coolify.io/docs/core/networking-in-coolify).
