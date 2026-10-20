from django.urls import path

from portal import views

urlpatterns = [
    path("", views.index, name="index"),
    path("forms/<slug:event_slug>/", views.event_form, name="event_form"),
    path("forms/<slug:event_slug>/uploads/", views.upload, name="upload"),
    path(
        "forms/<slug:event_slug>/uploads/<uuid:upload_id>/status/",
        views.upload_status,
        name="upload_status",
    ),
    path(
        "forms/<slug:event_slug>/uploads/<uuid:upload_id>/",
        views.upload_remove,
        name="upload_remove",
    ),
    path("forms/<slug:event_slug>/submit/", views.submit, name="submit"),
    path("received/<uuid:claim_id>/", views.received, name="received"),
    path("webhooks/mailersend/", views.mailersend_webhook, name="mailersend_webhook"),
    path("-/healthz", views.healthz, name="healthz"),
    path("-/readyz", views.readyz, name="readyz"),
]
