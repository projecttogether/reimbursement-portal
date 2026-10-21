from datetime import timedelta
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from portal.models import DraftAdmission


def client_network(request):
    """Trust forwarding only through explicitly configured proxy networks."""
    try:
        peer = ip_address(request.META.get("REMOTE_ADDR", ""))
    except ValueError:
        return "unknown"
    proxies = [ip_network(value) for value in settings.TRUSTED_PROXY_CIDRS]
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if any(peer in network for network in proxies) and len(forwarded) <= 1024:
        try:
            chain = [ip_address(value.strip()) for value in forwarded.split(",")]
        except ValueError:
            chain = []
        for address in reversed(chain):
            if not any(peer in network for network in proxies):
                break
            peer = address
    if peer.version == 6 and peer.ipv4_mapped:
        peer = peer.ipv4_mapped
    # IPv6 privacy addresses from the same /64 share a budget.
    return str(ip_network(f"{peer}/{64 if peer.version == 6 else 32}", strict=False))


@transaction.atomic
def reserve_draft(request):
    budget = locked_budget(request)
    if budget.count >= settings.MAX_DRAFTS_PER_NETWORK:
        return False
    budget.count += 1
    budget.save()
    return True


def locked_budget(request):
    key = salted_hmac("draft-admission", client_network(request)).hexdigest()
    budget, _ = DraftAdmission.objects.select_for_update().get_or_create(key=key)
    now = timezone.now()
    if budget.window_start <= now - timedelta(hours=24):
        budget.window_start, budget.count = now, 0
        budget.upload_count = budget.upload_bytes = 0
        budget.upload_window_start, budget.upload_window_count = now, 0
    return budget
