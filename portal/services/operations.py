"""Audited operator actions on claims, shared by the admin area and management commands."""

from django.db import transaction

from portal.job_kinds import RENDER
from portal.models import Claim, Operation
from portal.services import delivery, queue


class ActionRefused(Exception):
    """The action is not possible now. `code` names the reason; the message is German."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@transaction.atomic
def retry(claim_id, *, actor, reason, acknowledge_duplicate_risk=False):
    """Retry a failed render, or send again. Possible duplicates need acknowledgment."""
    claim = Claim.objects.select_for_update().get(pk=claim_id)
    if not reason.strip():
        raise ActionRefused("reason_missing", "Bitte gib einen Grund an.")
    if queue.claim_has_active_job(claim):
        raise ActionRefused("active_job", "Für diesen Antrag läuft bereits eine Verarbeitung.")
    if claim.state == Claim.State.READY:
        if not claim.pdf_key:
            raise ActionRefused(
                "pdf_removed",
                "Die PDF wurde bereits gelöscht und kann nicht erneut gesendet werden.",
            )
        try:
            delivery.retry(claim, acknowledge_duplicate_risk=acknowledge_duplicate_risk)
        except delivery.DuplicateRisk as exc:
            raise ActionRefused(
                "duplicate_risk",
                "Die E-Mail könnte bereits angekommen sein. Prüfe zuerst den Versand und "
                "bestätige dann das Risiko eines doppelten Versands.",
            ) from exc
    elif not RENDER.requeue(claim):
        raise ActionRefused(
            "not_retryable",
            "Dieser Antrag kann im aktuellen Zustand nicht erneut verarbeitet werden.",
        )
    Operation.objects.create(claim=claim, action="retry", actor=actor, reason=reason.strip()[:500])
    return claim
