from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from portal.services.documents import sandbox_ready
from portal.services.readiness import bounded_workspace
from portal.services.retention import purge_disabled_in_production


@register(Tags.security, deploy=True)
def production_checks(app_configs, **kwargs):
    errors = []
    if settings.DEBUG or settings.DEPLOYMENT_ENV == "development":
        errors.append(
            Error("Deployment checks require production or staging settings.", id="portal.E001")
        )
    if not settings.DOCUMENT_SANDBOX_ENABLED or not sandbox_ready():
        errors.append(
            Error(
                "Document sandbox requires Linux Landlock ABI 3+ and libseccomp2.", id="portal.E002"
            )
        )
    if not bounded_workspace():
        errors.append(
            Error(
                "Mount private/work as a private, bounded tmpfs of at most 750 MB.",
                id="portal.E003",
            )
        )
    return errors


# Not a deploy check: `migrate` and the worker run it on every production start.
@register()
def retention_checks(app_configs, **kwargs):
    if not purge_disabled_in_production():
        return []
    return [
        Warning(
            "Content purge is disabled: claims stay stored beyond the periods the privacy "
            "notice states.",
            hint="Set CONTENT_PURGE_ENABLED=1 once Finance has accepted the handoff.",
            id="portal.W001",
        )
    ]
