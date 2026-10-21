FROM ghcr.io/astral-sh/uv:0.12.24 AS uv
FROM python:3.13-slim-bookworm AS base
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core libseccomp2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 portal && useradd --uid 10001 --gid portal --create-home portal
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./

FROM base AS test
RUN uv sync --locked
COPY . .
RUN mkdir -p var/private && chown -R portal:portal /app
USER portal
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest", "-q"]

FROM base AS runtime
RUN uv sync --locked --no-dev
COPY . .
RUN DJANGO_DEBUG=1 DEPLOYMENT_ENV=development uv run --no-sync python manage.py collectstatic --noinput \
    && mkdir -p var/private && chown -R portal:portal /app
USER portal
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "90", "--access-logfile", "-", "--access-logformat", "request=%({x-request-id}o)s method=%(m)s status=%(s)s duration=%(L)s"]
