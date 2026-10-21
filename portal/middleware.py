import uuid

from django.conf import settings
from django.core.files.uploadhandler import StopUpload, TemporaryFileUploadHandler
from django.http import Http404, JsonResponse


class EventScopeMiddleware:
    """Expose this deployment's intake or shared webhook routes, as configured."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        role = settings.PORTAL_ROLE
        name = request.resolver_match.url_name
        if role == "standalone" or name in {"healthz", "readyz"}:
            return None
        if role == "webhook":
            if name != "mailersend_webhook":
                raise Http404
        elif role == "worker" or name == "mailersend_webhook":
            raise Http404
        elif "event_slug" in view_kwargs and view_kwargs["event_slug"] != settings.EVENT_SLUG:
            raise Http404
        return None


class BoundedUploadHandler(TemporaryFileUploadHandler):
    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > settings.MAX_RECEIPT_BYTES:
            self.request.upload_limit_exceeded = True
            raise StopUpload(connection_reset=True)
        return super().receive_data_chunk(raw_data, start)


class RequestLimitsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return JsonResponse({"error": "Ungültige Anfrage."}, status=400)
        cap = (
            settings.MAX_RECEIPT_BYTES + 128_000 if request.path.endswith("/uploads/") else 128_000
        )
        if length > cap:
            return JsonResponse({"error": "Die Datei oder Anfrage ist zu groß."}, status=413)
        return self.get_response(request)


class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Frame-Options"] = "DENY"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if not request.path.startswith("/static/"):
            response["Cache-Control"] = "no-store, private"
        return response


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response
