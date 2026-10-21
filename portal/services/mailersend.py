import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from email.message import EmailMessage

import httpx
from django.conf import settings

from portal.services.storage import outbox_key, path_for, write_bytes


@dataclass
class SendResult:
    state: str
    message_id: str = ""
    error_code: str = ""
    retry_after: int = 0


def client():
    return httpx.Client(
        base_url="https://api.mailersend.com/v1/",
        headers={
            "Authorization": f"Bearer {settings.MAILERSEND_API_TOKEN}",
            "Accept": "application/json",
        },
        timeout=httpx.Timeout(30, connect=10),
    )


def send_pdf(attempt):
    delivery, claim = attempt.delivery, attempt.delivery.claim
    if delivery.recipient.lower() not in settings.APPROVED_DELIVERY_RECIPIENTS:
        return SendResult("failed", error_code="unapproved_recipient")
    if (
        settings.TEST_RECIPIENT_ALLOWLIST
        and delivery.recipient.lower() not in settings.TEST_RECIPIENT_ALLOWLIST
    ):
        return SendResult("failed", error_code="test_recipient_blocked")
    content = path_for(claim.pdf_key).read_bytes()
    if (
        len(content) > settings.MAX_FINAL_PDF_BYTES
        or hashlib.sha256(content).hexdigest() != claim.pdf_hash
    ):
        return SendResult("failed", error_code="attachment_integrity")
    subject = f"Erstattungsantrag {claim.reference} | {claim.snapshot['name']}"
    text = f"Erstattungsantrag {claim.reference}\nVeranstaltung: {claim.snapshot['name']}\n\nDas vollständige Erstattungsformular und alle Belege sind in der beigefügten PDF enthalten.\n"
    filename = f"reimbursement_{claim.reference}.pdf"
    if settings.MAIL_DELIVERY_MODE == "local" and settings.DEBUG:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = (
            settings.MAIL_FROM_ADDRESS,
            delivery.recipient,
            subject,
        )
        message.set_content(text)
        message.add_attachment(content, maintype="application", subtype="pdf", filename=filename)
        write_bytes(outbox_key(attempt.id), message.as_bytes())
        return SendResult("delivered", f"local-{attempt.id}")
    payload = {
        "from": {"email": settings.MAIL_FROM_ADDRESS, "name": "ProjectTogether"},
        "to": [{"email": delivery.recipient}],
        "reply_to": {"email": settings.MAIL_REPLY_TO_ADDRESS},
        "subject": subject,
        "text": text,
        "attachments": [
            {
                "content": base64.b64encode(content).decode("ascii"),
                "filename": filename,
                "disposition": "attachment",
            }
        ],
        "tags": ["pt-reimbursement", f"attempt-{attempt.id}"],
        "settings": {"track_opens": False, "track_clicks": False, "track_content": False},
    }
    try:
        with client() as provider:
            response = provider.post("email", json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return SendResult("pending", error_code="provider_connection", retry_after=60)
    except httpx.TransportError:
        return SendResult("send_unknown", error_code="provider_outcome_unknown")
    if response.status_code == 429:
        try:
            delay = min(3600, max(60, int(response.headers.get("retry-after", "60"))))
        except ValueError:
            delay = 60
        return SendResult("pending", error_code="provider_throttled", retry_after=delay)
    if response.status_code >= 500:
        return SendResult("send_unknown", error_code="provider_outcome_unknown")
    if response.status_code != 202:
        return SendResult("failed", error_code="provider_rejected")
    try:
        warnings = response.json().get("warnings", []) if response.content else []
    except (ValueError, AttributeError):
        return SendResult("send_unknown", error_code="provider_response_invalid")
    if any(warning.get("type") in {"ALL_SUPPRESSED", "SOME_SUPPRESSED"} for warning in warnings):
        return SendResult("failed", error_code="recipient_suppressed")
    message_id = response.headers.get("x-message-id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", message_id):
        return SendResult("send_unknown", error_code="provider_message_id_missing")
    return SendResult(
        "paused"
        if response.headers.get("x-send-paused", "").lower() == "true"
        else "provider_accepted",
        message_id,
    )


WEBHOOK_TEST_SECRET = "test_Am3L1GuOIc4blLUuHqAPxxwkZaJyEk8G"


def verify_webhook(raw_body, signature, *, test=False):
    secret = WEBHOOK_TEST_SECRET if test else settings.MAILERSEND_WEBHOOK_SIGNING_SECRET
    if not secret or not isinstance(signature, str):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def retrieve_message(message_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", message_id):
        return None
    try:
        with client() as provider:
            response = provider.get(f"messages/{message_id}")
            response.raise_for_status()
            return response.json().get("data")
    except (httpx.HTTPError, ValueError):
        return None


def message_snapshot(message):
    """Only interpret explicitly documented email statuses, never an empty emails list."""
    if not isinstance(message, dict):
        return None
    emails = message.get("emails")
    if not isinstance(emails, list):
        return None
    for email in emails:
        if not isinstance(email, dict):
            continue
        status = email.get("status")
        if status == "rejected":
            # Rejection is terminal even when no webhook/activity was recorded.
            # Prefer documented suppression or bounce details when available.
            reason = email.get("suppression_reason")
            activities = email.get("activity") or []
            kinds = (
                {item.get("type") for item in activities if isinstance(item, dict)}
                if isinstance(activities, list)
                else set()
            )
            if reason == "hard_bounced" or "hard_bounced" in kinds:
                status = "hard_bounced"
            elif (
                reason in {"on_hold", "unsubscribed", "spam_complained", "blocklisted"}
                or "suppressed" in kinds
            ):
                status = "suppressed"
        if status in {
            "delivered",
            "hard_bounced",
            "soft_bounced",
            "suppressed",
            "sent",
            "queued",
            "rejected",
        }:
            return {
                "status": status,
                "recipient": email.get("to")
                or (
                    email.get("recipient", {}).get("email")
                    if isinstance(email.get("recipient"), dict)
                    else email.get("recipient")
                ),
                "id": email.get("id", ""),
            }
    return None
