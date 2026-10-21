"""Drafts: session-bound, token-authorized forms that become one claim on acceptance."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from portal.intake import IntakeError
from portal.models import Draft
from portal.services.draft_limits import reserve_draft


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def session_hash(request, *, create=False):
    if not request.session.session_key:
        if not create:
            return None
        request.session.create()
    return salted_hmac("draft-session", request.session.session_key).hexdigest()


def get_or_create_draft(request, form):
    """This session's editable draft of the form; a submitted draft starts a new one."""
    binding = session_hash(request)
    current = request.session.get(f"draft:{form.slug}", {})
    draft = (
        Draft.objects.filter(pk=current.get("id"), form=form, session_hash=binding).first()
        if current.get("id")
        else None
    )
    if (
        draft
        and not draft.claim_id
        and draft.expires_at > timezone.now()
        and draft.config_version == form.config_version
    ):
        return draft, current["token"]
    if (
        Draft.objects.filter(claim__isnull=True, expires_at__gt=timezone.now()).count()
        >= settings.MAX_ACTIVE_DRAFTS
    ):
        raise IntakeError(
            "Das Formular ist gerade ausgelastet. Bitte versuche es später erneut.", status=503
        )
    if (
        Draft.objects.filter(
            session_hash=binding, created_at__gt=timezone.now() - timedelta(days=1)
        ).count()
        >= 10
    ):
        raise IntakeError("Zu viele neue Formulare. Bitte versuche es später erneut.", status=429)
    if not reserve_draft(request):
        raise IntakeError(
            "Zu viele neue Formulare aus diesem Netzwerk. Bitte versuche es später erneut.",
            status=429,
        )
    # Reject before creating a session. Session creation must also commit outside
    # a transaction that can roll back after a normal admission rejection.
    binding = session_hash(request, create=True)
    token = secrets.token_urlsafe(32)
    draft = Draft.objects.create(
        form=form,
        session_hash=binding,
        token_hash=digest(token),
        config_version=form.config_version,
    )
    request.session[f"draft:{form.slug}"] = {"id": str(draft.id), "token": token}
    return draft, token


def authorize_draft(request, draft_id, token, form_slug, *, lock=False):
    try:
        draft_id = uuid.UUID(str(draft_id))
        query = Draft.objects.select_for_update() if lock else Draft.objects
        draft = query.get(pk=draft_id, form__slug=form_slug, session_hash=session_hash(request))
    except (Draft.DoesNotExist, ValueError, TypeError) as exc:
        raise IntakeError("Das Formular wurde nicht gefunden.", status=404) from exc
    if not isinstance(token, str) or not constant_time_compare(draft.token_hash, digest(token)):
        raise IntakeError("Das Formular wurde nicht gefunden.", status=404)
    if not draft.claim_id and draft.expires_at <= timezone.now():
        raise IntakeError(
            "Das Formular ist abgelaufen. Bitte öffne das Formular erneut.", status=410
        )
    return draft


def ensure_accepting(draft, form, form_version):
    """The draft can still change: not submitted, form open, same configuration revision."""
    if draft.claim_id:
        raise IntakeError("Dieses Formular wurde bereits eingereicht.", status=409)
    if not form.is_open():
        raise IntakeError("Dieses Formular ist für neue Anträge geschlossen.", status=409)
    if form_version != draft.config_version or form.config_version != draft.config_version:
        raise IntakeError(
            "Das Formular wurde aktualisiert. Bitte lade es neu und prüfe die Angaben erneut.",
            status=409,
        )
