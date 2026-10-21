#!/bin/sh
set -eu

if [ "${PORTAL_ROLE:-standalone}" = "web" ]; then
    python manage.py configure_event --from-env
fi

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 90 \
    --access-logfile - \
    --access-logformat 'request=%({x-request-id}o)s method=%(m)s status=%(s)s duration=%(L)s' \
    "$@"
