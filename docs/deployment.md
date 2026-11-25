# Deployment

Use `compose.yaml` for the web process, worker, scanner and migration job on a Linux
server with Debian 13 or later, or a similar system with systemd and apt such as Ubuntu
24.04. The document sandbox needs Linux 6.2 or later (Landlock ABI 3); Debian 12 runs
Linux 6.1. The file runs the image that the Deploy workflow builds from `main`;
`PORTAL_IMAGE_TAG` chooses it: `latest` for the newest commit, or `sha-<commit>` for one
build ([builds and deploys](releases.md)). Caddy on the same server terminates HTTPS and
routes the public hostname to the web service only. Keep all persistent volumes across
upgrades.

## Database and storage

Use a PostgreSQL of your own that the containers reach over the network, and set
`DATABASE_URL` to its URL; `localhost` in it would be the container itself. Or run one
next to the app with `compose.postgres.yaml`: add to `.env`, with the same
`openssl rand -hex 32` value in both places,

```sh
COMPOSE_FILE=compose.yaml:compose.postgres.yaml
POSTGRES_PASSWORD=REPLACE_WITH_RANDOM_HEX_PASSWORD
DATABASE_URL=postgresql://reimbursements:REPLACE_WITH_RANDOM_HEX_PASSWORD@postgres:5432/reimbursements
```

With `COMPOSE_FILE` in `.env`, every `docker compose` command in `/srv/reimbursements`
uses both files. Keep database and private-file backups together. Docker Compose names
the volumes after the folder: `reimbursements_private_data` holds the private files,
`reimbursements_scanner_data` the scanner's signature database, and
`reimbursements_postgres` the database of `compose.postgres.yaml`. Web and worker also
get a bounded 700 MB tmpfs workspace.

## Admin sign-in

Create a confidential OpenID Connect client in your Keycloak realm. Enable the
authorization code flow, disable implicit and direct password grants, and enable
PKCE S256. Configure the following against your public hostname:

| Setting | Example |
| --- | --- |
| Redirect URI | `https://reimbursements.example.invalid/oidc/callback/` |
| Web origin | `https://reimbursements.example.invalid` |
| Post-logout redirect | `https://reimbursements.example.invalid/` |

Add a group membership mapper that includes full paths in the verified ID token.
Only users with a verified email and a mapped portal group may sign in.

| Environment variable | Default group | Access |
| --- | --- | --- |
| `KEYCLOAK_ADMIN_GROUP` | `/reimbursements/admins` | Templates, notices, all forms and claims |
| `KEYCLOAK_EDITOR_GROUP` | `/reimbursements/editors` | Own forms and their submissions |

Set `KEYCLOAK_BASE_URL`, `KEYCLOAK_REALM`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
Membership is refreshed at every sign-in. Admin sessions expire after eight hours.

## Application settings

Create `.env` from `.env.production.example` and replace all placeholder values
([set up](#set-up), step 4). Set the public hostname in `ALLOWED_HOSTS`, the HTTPS
origin in `CSRF_TRUSTED_ORIGINS`, and the address Caddy's requests come from in
`TRUSTED_PROXY_CIDRS` ([set up](#set-up), step 6). Include `127.0.0.1` in allowed hosts
for container probes.

Set `PORTAL_OPERATOR` and `PORTAL_LEGAL_URL`. The app language, timezone and date
format are set after the first sign-in under Admin > General, the accent color and
favicon under Admin > Appearance. `APPROVED_DELIVERY_RECIPIENTS` lists every inbox a
form may send to. For staging, also set a nonempty `TEST_RECIPIENT_ALLOWLIST`; it
restricts accounting and participant messages. Do not make production receipts
available to staging.

Set up email after the first sign-in under Admin > Email, and send a test email from
there. Forms take no claims until it is set up. Choose one provider:

- **MailerSend**: enter an API token, and a sender address on a verified MailerSend
  domain. Create a version 2 webhook at
  `https://reimbursements.example.invalid/webhooks/mailersend/`, using your actual
  hostname, subscribe it to the supported sent, delivered, deferred, bounced and
  suppressed events, and enter its signing secret. Disable provider tracking. The
  page shows when the last signed event arrived.
- **SMTP server**: enter the server, port, encryption (STARTTLS, TLS, or none for a
  relay in a trusted network) and, if it needs a login, username and password. SMTP
  reports no deliveries or bounces back: an email counts as delivered once the server
  accepts it, and bounces go to the sender address.

Also enter the reply address for Finance emails, a monitored inbox. Tokens, signing
secrets and passwords entered there are stored encrypted with a key derived from
`DJANGO_SECRET_KEY`; after changing that key, enter them again. Instead of entering them, you can set `MAILERSEND_API_TOKEN`
(which chooses MailerSend), `MAILERSEND_WEBHOOK_SIGNING_SECRET`, `MAIL_FROM_ADDRESS` and
`MAIL_REPLY_TO_ADDRESS` in the environment; a value set there wins and the admin area
shows it without letting anyone change it. See [operations](operations.md) for
ambiguous delivery outcomes and retry controls.

Optionally set `SENTRY_DSN` for error reports from web, worker and migrations. See
[operations](operations.md#monitoring-and-maintenance) for what a report contains.

## Set up

Run the commands as root (`sudo -i`).

1. **Docker:** install Docker Engine and the Compose plugin from Docker's apt
   repository ([Debian](https://docs.docker.com/engine/install/debian/),
   [Ubuntu](https://docs.docker.com/engine/install/ubuntu/)), and Git:
   `apt install git`.
2. **Get the files:**

   ```sh
   git clone https://github.com/projecttogether/reimbursement-portal.git /srv/reimbursements
   cd /srv/reimbursements
   ```

3. **Admin sign-in:** create the Keycloak client and its groups as
   [admin sign-in](#admin-sign-in) describes.
4. **Create `.env`** from `.env.production.example`
   (`cp .env.production.example .env`, `chmod 600 .env`; not `.env.example`, which
   selects the local setup) and replace all placeholder values as
   [application settings](#application-settings) describes. Set `DATABASE_URL` as
   [database and storage](#database-and-storage) describes, and `TRUSTED_PROXY_CIDRS`
   to `127.0.0.1/32` until step 6.
5. **Start:** `docker compose up -d --pull always`. The scanner's first start can take
   several minutes.
6. **Trusted proxy:** Caddy's requests reach `web` from the gateway of the app's Docker
   network. Show it with:

   ```sh
   docker network inspect reimbursements_default --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'
   ```

   Set `TRUSTED_PROXY_CIDRS` in `.env` to it with `/32`, such as `172.18.0.1/32`, and
   run `docker compose up -d` again.
7. **HTTPS:** point the domain's DNS at the server, install Caddy (`apt install caddy`),
   and put this site into `/etc/caddy/Caddyfile`, in place of the package's example site
   `:80` and next to the sites of other apps:

   ```text
   reimbursements.example.invalid {
   	request_body {
   		max_size 10MiB
   	}
   	reverse_proxy 127.0.0.1:8000 {
   		buffer_requests
   		max_buffer_size 10MiB
   	}
   }
   ```

   Then `systemctl reload caddy`. Caddy gets the certificate itself. It refuses a
   request larger than 10 MiB and receives a smaller one in full before it passes it
   on, so a slowly sent upload does not hold one of the web process's workers. Debian
   13's package is Caddy 2.6; Caddy 2.8 and later take `request_buffers 10MiB` instead
   of the two lines under `reverse_proxy`.

   `compose.yaml` publishes `web` on `127.0.0.1:8000` only; `PORTAL_PORT`
   changes the host port. With several apps on one server, give each its own port
   and its own site in the Caddyfile.
8. **First launch:** sign in at `/admin/` and continue as
   [first launch](#first-launch) describes. Run the setup check
   in the web container, with one of your approved inboxes in place of the example:

   ```sh
   docker compose exec web python manage.py verify_setup --to finance@your-org.example.invalid
   ```

## First launch

Deploy, then sign in at `/admin/`. Replace the initial privacy setup text with your
operator's notice, review the template declaration, and create a form with an
approved recipient, support address and required internal values. Then run
`python manage.py verify_setup --to <an approved inbox>` in the web container.
It sends a synthetic claim through the scanner, document sandbox, the email provider
and, with MailerSend, the signed delivery webhook, and deletes it again; see
[setup check](operations.md#setup-check). Publish your form and send a synthetic claim
through it before accepting participant data, and verify its PDF.

Run the [release checks](production-readiness.md). The migration job must complete
before web and worker start.
Before an upgrade, read the pull requests merged since the deployed commit; a pull
request labelled `breaking` says what operators must do ([builds and
deploys](releases.md)).

## Backups

Back up PostgreSQL and the private files together, encrypted and off-host:
[Operations](operations.md#backup-and-restore) has the rules. Keep `DJANGO_SECRET_KEY`
apart from the backups (open drafts of participants and the secrets entered under
Admin > Email are tied to it): a restore needs it, and the backups do not hold it.

## Upgrades

Back up the database and private files together. Then, in `/srv/reimbursements`:

1. Run `git pull` for the newest `compose.yaml`.
2. Take the upgrade steps of `breaking` pull requests, and set `PORTAL_IMAGE_TAG` in
   `.env` if you pin a build.
3. Stop the old web and worker before the migrations: `docker compose stop web worker`.
4. Run `docker compose up -d --pull always`. The migration job completes before web and
   worker start. Keep the database, private files and scanner volumes.

Then run the [setup check](operations.md#setup-check) again.

## From the TypeScript version

Versions 0.5 and 0.6 of this app were written in TypeScript. This version runs on their
database as it is: they kept the tables of the Django version, recorded its migration
`0020_email_settings` and sealed the secrets of Admin > Email the same way. Keep
`DJANGO_SECRET_KEY`: the secrets were sealed with it (with `AUTH_SECRET` only when
`DJANGO_SECRET_KEY` was not set; then set `DJANGO_SECRET_KEY` to that value). Set
`PORTAL_OPERATOR` and `PORTAL_LEGAL_URL` again: the TypeScript version kept them under
Admin > General, which this version does not read. Admins sign in again. The tables and
columns that only the TypeScript version used stay and do no harm.
