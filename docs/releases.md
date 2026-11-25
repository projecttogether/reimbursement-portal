# Builds and deploys

Every commit on `main` that passes CI is built and deployed. There are no numbered
releases: the commit is the version.

## On every pull request

The [CI workflow](../.github/workflows/ci.yaml) checks the history for secrets, the
tracked files for private artifacts, lint, formatting, the Django configuration,
migration drift, the PostgreSQL regression suite and document confinement, builds the
production image, and runs that image with a worker for the browser and setup checks.

Label a pull request `breaking` when operators must act on it (a new required
setting, a step before or after the deploy), and write the steps into its
description. Merging deploys it, so take steps that must come first before merging.

## After the merge

The [Deploy workflow](../.github/workflows/deploy.yml) starts when CI passes on
`main`, or by hand (Actions > Deploy > Run workflow), for example after a setting
changed:

1. It builds the production image with `APP_VERSION` set to the commit and pushes it as
   `ghcr.io/projecttogether/reimbursement-portal:sha-<commit>` and `:latest`.
2. It sends the image and the app's settings to the server of the GitHub environment
   `production` over SSH. The server's deploy script pulls the image, runs the
   migrations, starts web and worker, and puts the previous image and settings back
   when they do not become healthy. One deploy runs at a time; a newer one waits.

Without the variable `DEPLOY_HOST` in the environment `production`, step 2 deploys
nothing.

## The environment production

The environment holds what the deploy sends: the app's own settings and the way to the
server. Settings of the server itself (`DATABASE_URL`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `TRUSTED_PROXY_CIDRS`, `HEALTHCHECK_TOKEN`) live on the server
and win over these.

| Kind | Names |
| --- | --- |
| Secrets | `DEPLOY_SSH_KEY`, `DJANGO_SECRET_KEY`, `OIDC_CLIENT_SECRET`, `MAILERSEND_API_TOKEN`, `MAILERSEND_WEBHOOK_SIGNING_SECRET`, `SENTRY_DSN` |
| Variables | `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`, `APP_URL`, `OIDC_CLIENT_ID`, `KEYCLOAK_BASE_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_ADMIN_GROUP`, `KEYCLOAK_EDITOR_GROUP`, `APPROVED_DELIVERY_RECIPIENTS`, `TEST_RECIPIENT_ALLOWLIST`, `PORTAL_OPERATOR`, `PORTAL_LEGAL_URL`, `MAIL_FROM_ADDRESS`, `MAIL_REPLY_TO_ADDRESS`, `SENTRY_ENVIRONMENT` |

Empty values are left out. A new setting needs a line in the workflow's `SETTINGS` and
its own line below it.

## Images

The images set `APP_VERSION` to the first 12 characters of the commit. `/-/healthz`
reports it, Admin > General shows it, and error reports name the release
`reimbursement-portal@<commit>` unless `SENTRY_RELEASE` is set. Images built from
source have no version.

The package `reimbursement-portal` in GitHub's container registry must be public, so
that other servers can pull it without a token: an owner of the organization sets
that once, after the first build.

## Rollbacks

A deploy that does not become healthy puts the previous image back by itself. To go
back by hand, revert the change on `main`, or run the server's rollback. Neither
undoes database migrations, so a migration keeps the previous version working: add
columns to existing tables with a database default.
