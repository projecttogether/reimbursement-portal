"""The admin area: templates, forms, the privacy notice and claims, behind PT Login."""

import uuid

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.text import slugify
from mozilla_django_oidc.views import OIDCLogoutView

from portal import admin_navigation, roles
from portal.models import (
    Claim,
    Form,
    FormTemplate,
    Operation,
    PrivacyNotice,
    TemplateField,
)
from portal.roles import is_form_editor, is_template_admin
from portal.services import delivery, operations
from portal.templatetags.ui import render_component


class PortalAdminSite(admin.AdminSite):
    site_header = "ProjectTogether Erstattungsportal"
    site_title = "Erstattungsportal"
    index_title = "Verwaltung"
    site_url = "/"
    enable_nav_sidebar = False

    def has_permission(self, request):
        return is_form_editor(request.user)

    def login(self, request, extra_context=None):
        next_url = request.GET.get("next", "")
        if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
            next_url = reverse("admin:index")
        if self.has_permission(request):
            return HttpResponseRedirect(next_url or reverse("admin:index"))
        if settings.OIDC_ENABLED:
            query = urlencode({"next": next_url or reverse("admin:index")})
            return HttpResponseRedirect(f"{reverse('oidc_authentication_init')}?{query}")
        if settings.DEBUG:
            # Local development without Keycloak: `manage.py createsuperuser`.
            return super().login(request, extra_context)
        return render(request, "admin/portal/no_access.html", {"unconfigured": True}, status=503)

    def logout(self, request, extra_context=None):
        if settings.OIDC_ENABLED and request.method == "POST":
            return OIDCLogoutView.as_view()(request)
        return super().logout(request, extra_context)

    def each_context(self, request):
        context = super().each_context(request)
        if self.has_permission(request):
            context["nav_sections"] = admin_navigation.sections(request)
            context["settings_tabs"] = admin_navigation.settings_tabs(request)
            context["account_url"] = settings.KEYCLOAK_ACCOUNT_URL
        return context

    def index(self, request, extra_context=None):
        return redirect("admin:portal_claim_changelist")

    def app_index(self, request, app_label, extra_context=None):
        return redirect("admin:portal_claim_changelist")

    def get_urls(self):
        return [
            path("kein-zugriff/", self.no_access, name="no_access"),
            path("suche/", self.admin_view(self.search), name="search"),
            path(
                "suche/vorschlaege/",
                self.admin_view(self.search_suggestions),
                name="search_suggestions",
            ),
            *super().get_urls(),
        ]

    def no_access(self, request):
        return render(request, "admin/portal/no_access.html", status=403)

    def search(self, request):
        query = request.GET.get("q", "")[:100]
        groups = admin_navigation.search(request, self, query, admin_navigation.RESULT_LIMIT)
        return TemplateResponse(
            request,
            "admin/portal/search.html",
            {**self.each_context(request), "title": "Suche", "query": query, "groups": groups},
        )

    def search_suggestions(self, request):
        query = request.GET.get("q", "")[:100]
        groups = admin_navigation.search(request, self, query, admin_navigation.SUGGESTION_LIMIT)
        return JsonResponse({"groups": groups})


site = PortalAdminSite(name="admin")


class RoleAdmin(admin.ModelAdmin):
    """Template admins may change; form editors may only view."""

    def has_module_permission(self, request):
        return is_form_editor(request.user)

    def has_view_permission(self, request, obj=None):
        return is_form_editor(request.user)

    def has_add_permission(self, request):
        return is_template_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_template_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_template_admin(request.user)


@admin.register(PrivacyNotice, site=site)
class PrivacyNoticeAdmin(RoleAdmin):
    list_description = (
        "Teilnehmende sehen immer die neueste Version. Jede Änderung wird als neue Version "
        "gespeichert."
    )
    add_label = "Neue Version"
    list_display = ["__str__", "created_at", "created_by", "is_current"]
    fields = ["text"]

    def has_module_permission(self, request):
        return is_template_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_template_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Aktuell", boolean=True)
    def is_current(self, obj):
        current = PrivacyNotice.current()
        return bool(current and current.pk == obj.pk)

    def get_form(self, request, obj=None, change=False, **kwargs):
        kwargs["help_texts"] = {
            "text": "Eine Leerzeile trennt Absätze. Eine Zeile mit „# “ am Anfang wird zur Überschrift."
        }
        return super().get_form(request, obj, change, **kwargs)

    def add_view(self, request, form_url="", extra_context=None):
        extra_context = {**(extra_context or {}), "show_save_and_add_another": False}
        return super().add_view(request, form_url, extra_context)

    def get_changeform_initial_data(self, request):
        current = PrivacyNotice.current()
        return {"text": current.text} if current else {}

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user.email or request.user.get_username()
        obj.save()


class TemplateFieldInline(admin.TabularInline):
    model = TemplateField
    extra = 0
    fields = ["position", "label", "key", "kind", "choices", "pattern", "required"]

    def has_view_permission(self, request, obj=None):
        return is_form_editor(request.user)

    def has_add_permission(self, request, obj=None):
        return is_template_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_template_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_template_admin(request.user)


@admin.register(FormTemplate, site=site)
class FormTemplateAdmin(RoleAdmin):
    list_description = (
        "Vorlagen legen fest, welche internen Werte und Angaben ein Formular abfragt."
    )
    add_label = "Vorlage anlegen"
    search_placeholder = "Name oder Beschreibung …"
    list_display = ["name", "travel", "mileage_enabled", "form_count", "updated_at"]
    search_fields = ["name", "description"]
    inlines = [TemplateFieldInline]
    fieldsets = [
        (None, {"fields": ["name", "description"]}),
        ("Für Teilnehmende", {"fields": ["intro", "travel", "mileage_enabled", "mileage_rate"]}),
        ("Erklärung", {"fields": ["declaration", "declaration_version"]}),
    ]

    @admin.display(description="Formulare")
    def form_count(self, obj):
        return obj.forms.count()

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.forms.exists())


class FormAdminForm(forms.ModelForm):
    """Adds one field per template field; values are stored in `field_values`."""

    portal_template = None
    portal_status = Form.Status.DRAFT

    class Meta:
        model = Form
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.template_id:
            self.instance.template = self.portal_template
        self.instance.status = self.portal_status
        if self.portal_status == Form.Status.DRAFT:
            for name, field in self.fields.items():
                if name in {"name", "slug", "recipient", "support_email"} or name.startswith(
                    "value_"
                ):
                    field.required = False
        for field in self.portal_template.fields.all():
            self.initial.setdefault(
                f"value_{field.key}", self.instance.field_values.get(field.key, "")
            )
        if "recipient" in self.fields:
            choices = [(address, address) for address in settings.APPROVED_DELIVERY_RECIPIENTS]
            self.fields["recipient"] = forms.ChoiceField(
                label=self.fields["recipient"].label,
                choices=[("", "—"), *choices],
                required=self.portal_status == Form.Status.PUBLISHED,
            )

    def clean(self):
        data = super().clean()
        if self.portal_status == Form.Status.DRAFT:
            if "name" not in self.errors and not data.get("name"):
                data["name"] = "Neues Formular"
            if "slug" not in self.errors and not data.get("slug"):
                stem = slugify(data.get("name", ""))[:30] or "formular"
                data["slug"] = self.instance.slug or f"{stem}-{uuid.uuid4().hex[:12]}"
        values = {}
        for field in self.portal_template.fields.all():
            name = f"value_{field.key}"
            if self.portal_status == Form.Status.DRAFT and not data.get(name):
                values[field.key] = ""
                continue
            try:
                values[field.key] = field.validate(data.get(name, ""))
            except ValidationError as exc:
                self.add_error(name, exc)
        self.instance.field_values = values
        start, end = data.get("trip_start"), data.get("trip_end")
        if start and end and start > end:
            self.add_error("trip_end", "Das Reiseende darf nicht vor dem Reisebeginn liegen.")
        opens, closes = data.get("opens_at"), data.get("closes_at")
        if opens and closes and opens >= closes:
            self.add_error("closes_at", "Das Ende muss nach dem Beginn liegen.")
        return data


def value_field(field):
    if field.kind == TemplateField.Kind.CHOICE:
        return forms.ChoiceField(
            label=field.label,
            required=field.required,
            choices=[("", "—"), *((option, option) for option in field.options())],
        )
    return forms.CharField(label=field.label, required=field.required, max_length=200)


@admin.register(Form, site=site)
class FormAdmin(admin.ModelAdmin):
    change_form_template = "admin/portal/form/change_form.html"
    delete_confirmation_template = "admin/portal/form/delete_confirmation.html"
    list_description = "Über den Link eines Formulars reichen Teilnehmende ihre Auslagen ein."
    add_label = "Formular anlegen"
    search_placeholder = "Name oder Kurzname …"
    empty_description = "Lege ein Formular aus einer Vorlage an und teile den Link."
    list_display = [
        "name",
        "template",
        "publication_status",
        "primary",
        "values_complete",
        "open_now",
        "closes_at",
        "link",
    ]
    list_filter = ["status", "primary", "template"]
    search_fields = ["name", "slug"]
    exclude = ["status", "creation_key"]
    prepopulated_fields = {"slug": ["name"]}

    def has_module_permission(self, request):
        return is_form_editor(request.user)

    def has_view_permission(self, request, obj=None):
        return is_form_editor(request.user) and (obj is None or roles.manages(request.user, obj))

    def has_add_permission(self, request):
        return is_form_editor(request.user)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return is_template_admin(request.user) and (
            obj is None or (obj.status == Form.Status.DRAFT and not obj.claims.exists())
        )

    def delete_view(self, request, object_id, extra_context=None):
        if request.method == "POST" and request.POST.get("post") != "yes":
            return JsonResponse({"error": "Bitte bestätige das Löschen des Entwurfs."}, status=400)
        return super().delete_view(request, object_id, extra_context)

    def get_queryset(self, request):
        return roles.managed_forms(
            request.user, super().get_queryset(request).select_related("template")
        )

    def _template(self, request, obj):
        if obj is not None:
            return obj.template
        try:
            return FormTemplate.objects.get(pk=request.GET.get("template"))
        except (FormTemplate.DoesNotExist, ValueError, TypeError) as exc:
            raise Http404 from exc

    def add_view(self, request, form_url="", extra_context=None):
        if "template" not in request.GET:
            return TemplateResponse(
                request,
                "admin/portal/form/choose_template.html",
                {
                    **self.admin_site.each_context(request),
                    "opts": self.model._meta,
                    "title": "Vorlage für das neue Formular wählen",
                    "templates": FormTemplate.objects.all(),
                },
            )
        return super().add_view(request, form_url, extra_context)

    def get_form(self, request, obj=None, change=False, **kwargs):
        template = self._template(request, obj)
        attributes = {f"value_{field.key}": value_field(field) for field in template.fields.all()}
        attributes["portal_template"] = template
        status = obj.status if obj else Form.Status.DRAFT
        if request.method == "POST":
            action = request.POST.get("_publication")
            if action == "publish":
                status = Form.Status.PUBLISHED
            elif action == "unpublish":
                status = Form.Status.DRAFT
        attributes["portal_status"] = status
        kwargs["form"] = type("FormAdminForm", (FormAdminForm,), attributes)
        return super().get_form(request, obj, change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        template = self._template(request, obj)
        main = ["template_name", "name", "slug", "primary", "opens_at", "closes_at"]
        if obj is not None:
            main.append("link")
        fieldsets = [(None, {"fields": main})]
        values = [f"value_{field.key}" for field in template.fields.all()]
        if values:
            fieldsets.append(
                (
                    "Interne Werte",
                    {
                        "fields": values,
                        "description": "Nicht sichtbar für Teilnehmende, erscheinen in der PDF.",
                    },
                )
            )
        participant = ["instructions", "purpose_options"]
        if template.travel != FormTemplate.Travel.OFF:
            participant += ["trip_start", "trip_end"]
        fieldsets += [
            ("Für Teilnehmende", {"fields": participant}),
            ("Versand und Kontakt", {"fields": ["recipient", "support_email"]}),
        ]
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        # The start page is app-wide, so only template admins choose it.
        if is_template_admin(request.user):
            return ["template_name", "link"]
        return ["template_name", "link", "primary"]

    def get_prepopulated_fields(self, request, obj=None):
        return self.prepopulated_fields if obj is None else {}

    def get_changeform_initial_data(self, request):
        recipients = settings.APPROVED_DELIVERY_RECIPIENTS
        return {
            "support_email": request.user.email,
            "recipient": recipients[0] if len(recipients) == 1 else "",
        }

    @admin.display(description="Status", ordering="status")
    def publication_status(self, obj):
        return render_component(
            "badge",
            obj.get_status_display(),
            variant="success" if obj.status == Form.Status.PUBLISHED else "secondary",
        )

    @admin.display(description="Vorlage")
    def template_name(self, obj):
        return obj.template.name if obj and obj.template_id else ""

    @admin.display(description="Link", ordering="slug")
    def link(self, obj):
        if not obj or not obj.pk:
            return ""
        url = self._absolute(reverse("form_page", kwargs={"form_slug": obj.slug}))
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url)

    def _absolute(self, url):
        request = getattr(self, "_request", None)
        return request.build_absolute_uri(url) if request else url

    @admin.display(description="Jetzt geöffnet", boolean=True)
    def open_now(self, obj):
        return obj.is_open()

    @admin.display(description="Interne Werte vollständig", boolean=True)
    def values_complete(self, obj):
        return not obj.internal_value_errors()

    def changelist_view(self, request, extra_context=None):
        self._request = request
        return super().changelist_view(request, extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        self._request = request
        creation_key = None
        if self._autosave(request) and object_id is None and request.POST.get("_creation_key"):
            try:
                creation_key = uuid.UUID(request.POST["_creation_key"])
            except ValueError:
                return JsonResponse(
                    {"error": "Ungültige Anfrage. Bitte lade die Seite neu."}, status=400
                )
            existing = self.get_queryset(request).filter(creation_key=creation_key).first()
            if existing:
                object_id = str(existing.pk)
        form = self.get_object(request, object_id) if object_id else None
        extra_context = {
            **(extra_context or {}),
            "form_delete_hidden": not (form and self.has_delete_permission(request, form)),
        }
        if (
            request.method == "GET"
            and form
            and form.status == Form.Status.PUBLISHED
            and form.internal_value_errors()
        ):
            messages.warning(
                request,
                "Die Vorlage wurde geändert. Das Formular bleibt geschlossen, bis diese "
                f"internen Werte passen: {', '.join(form.internal_value_errors())}.",
            )
        if request.method == "POST" and request.POST.get("_publication", "") not in {
            "",
            "publish",
            "unpublish",
        }:
            return JsonResponse({"error": "Ungültiger Statuswechsel."}, status=400)
        try:
            response = super().changeform_view(request, object_id, form_url, extra_context)
        except IntegrityError:
            # A timed-out first save may still finish while its retry is being processed.
            existing = (
                self.get_queryset(request).filter(creation_key=creation_key).first()
                if creation_key and object_id is None
                else None
            )
            if existing is None:
                raise
            return self.changeform_view(request, str(existing.pk), form_url, extra_context)
        if self._autosave(request) and isinstance(response, TemplateResponse):
            context = response.context_data
            if context.get("adminform"):
                return JsonResponse(
                    {"errors": context["adminform"].form.errors.get_json_data()}, status=400
                )
        if self._autosave(request) and response.status_code == 302:
            # A deleted form must never turn an autosave into a second new form.
            return JsonResponse(
                {"error": "Das Formular ist nicht mehr verfügbar. Bitte lade die Seite neu."},
                status=404,
            )
        return response

    @staticmethod
    def _autosave(request):
        return request.method == "POST" and request.POST.get("_autosave") == "1"

    def _saved_response(self, request, obj):
        return JsonResponse(
            {
                "url": reverse("admin:portal_form_change", args=[obj.pk]),
                "name": obj.name,
                "slug": obj.slug,
                "status": obj.status,
                "status_label": obj.get_status_display(),
                "link": reverse("form_page", kwargs={"form_slug": obj.slug}),
                "can_delete": self.has_delete_permission(request, obj),
                "delete_url": reverse("admin:portal_form_delete", args=[obj.pk]),
            }
        )

    def response_add(self, request, obj, post_url_continue=None):
        if self._autosave(request):
            return self._saved_response(request, obj)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if self._autosave(request):
            return self._saved_response(request, obj)
        return super().response_change(request, obj)

    def save_model(self, request, obj, form, change):
        if not change and self._autosave(request) and request.POST.get("_creation_key"):
            obj.creation_key = uuid.UUID(request.POST["_creation_key"])
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change or not is_template_admin(request.user):
            # Editors cannot remove themselves from a form they are editing.
            form.instance.owners.add(request.user)


DELIVERY_STATUS = {
    "pending": "Versand wartet",
    "sending": "Wird versendet",
    "provider_accepted": "Vom Versanddienst angenommen",
    "paused": "Versand verzögert",
    "delivered": "Zugestellt",
    "failed": "Versand fehlgeschlagen",
    "send_unknown": "Versandstatus unklar",
}


# Badge tones of claim and delivery states.
STATUS_TONES = {
    Claim.State.FAILED: "destructive",
    Claim.State.PURGING: "outline",
    Claim.State.PURGED: "outline",
    "paused": "warning",
    "send_unknown": "warning",
    "failed": "destructive",
    "delivered": "success",
}


def claim_status_parts(claim):
    """The claim state, or the delivery state once a delivery exists."""
    current = getattr(claim, "delivery", None)
    if claim.state in {Claim.State.PURGING, Claim.State.PURGED} or current is None:
        return claim.state, claim.get_state_display()
    return current.state, DELIVERY_STATUS.get(current.state, current.state)


def claim_status(claim):
    return claim_status_parts(claim)[1]


def claim_status_badge(claim):
    key, label = claim_status_parts(claim)
    return render_component("badge", label, variant=STATUS_TONES.get(key, "secondary"))


class RetryForm(forms.Form):
    reason = forms.CharField(
        label="Grund", max_length=500, widget=forms.Textarea(attrs={"rows": 3})
    )
    acknowledge_duplicate_risk = forms.BooleanField(
        label=(
            "Ich habe den Versand geprüft und nehme in Kauf, dass Finance den Antrag "
            "doppelt erhalten kann."
        ),
        required=False,
    )


class OperationInline(admin.TabularInline):
    model = Operation
    extra = 0
    fields = ["when", "what", "who", "why"]
    readonly_fields = fields
    can_delete = False
    verbose_name = "Aktion"
    verbose_name_plural = "Protokollierte Aktionen"

    def has_view_permission(self, request, obj=None):
        return is_form_editor(request.user)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Zeitpunkt")
    def when(self, obj):
        return obj.created_at

    @admin.display(description="Aktion")
    def what(self, obj):
        return obj.action

    @admin.display(description="Durch")
    def who(self, obj):
        return obj.actor

    @admin.display(description="Grund")
    def why(self, obj):
        return obj.reason


@admin.register(Claim, site=site)
class ClaimAdmin(admin.ModelAdmin):
    list_description = "Eingereichte Anträge und ihr Verarbeitungsstand."
    search_placeholder = "Name, E-Mail oder Referenz …"
    empty_description = "Sobald Teilnehmende ein Formular einreichen, erscheint der Antrag hier."
    list_display = [
        "short_reference",
        "form",
        "submitted_at",
        "full_name",
        "email",
        "total",
        "status",
        "overdue",
    ]
    list_filter = [("form", admin.RelatedOnlyFieldListFilter), "state", "delivery__state"]
    search_fields = ["full_name", "email"]
    date_hierarchy = "submitted_at"
    inlines = [OperationInline]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "reference_display",
                    "form",
                    "submitted_at",
                    "full_name",
                    "email",
                    "total",
                ]
            },
        ),
        (
            "Verarbeitung",
            {"fields": ["status", "error", "pdf_removed_at", "retention_due", "overdue"]},
        ),
    ]
    readonly_fields = [
        "reference_display",
        "form",
        "submitted_at",
        "full_name",
        "email",
        "total",
        "status",
        "error",
        "pdf_removed_at",
        "retention_due",
        "overdue",
    ]

    def has_module_permission(self, request):
        return is_form_editor(request.user)

    def has_view_permission(self, request, obj=None):
        return is_form_editor(request.user) and (obj is None or roles.sees(request.user, obj))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return roles.visible_claims(
            request.user, super().get_queryset(request).select_related("form", "delivery")
        )

    def get_search_results(self, request, queryset, search_term):
        try:
            # A participant quotes the full reference.
            return queryset.filter(pk=uuid.UUID(search_term.strip())), False
        except ValueError:
            return super().get_search_results(request, queryset, search_term)

    @admin.display(description="Referenz")
    def reference_display(self, obj):
        return obj.reference

    @admin.display(description="Referenz", ordering="id")
    def short_reference(self, obj):
        return str(obj.id)[:8]

    @admin.display(description="Status")
    def status(self, obj):
        return claim_status_badge(obj)

    @admin.display(description="Fehler")
    def error(self, obj):
        current = getattr(obj, "delivery", None)
        return obj.error_code or (current.error_code if current else "") or "—"

    @admin.display(description="Klärung")
    def overdue(self, obj):
        if (
            obj.resolution_due <= timezone.now()
            and not obj.retention_due
            and obj.state != Claim.State.PURGED
        ):
            return render_component("badge", "Überfällig", variant="warning")
        return "—"

    def get_urls(self):
        return [
            path(
                "<uuid:claim_id>/erneut-versuchen/",
                self.admin_site.admin_view(self.retry_view),
                name="portal_claim_retry",
            ),
            *super().get_urls(),
        ]

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "can_retry": is_template_admin(request.user),
            "retry_url": reverse("admin:portal_claim_retry", args=[object_id]),
        }
        return super().change_view(request, object_id, form_url, extra_context)

    def retry_view(self, request, claim_id):
        if not is_template_admin(request.user):
            raise PermissionDenied
        claim = get_object_or_404(Claim.objects.select_related("form"), pk=claim_id)
        form = RetryForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                operations.retry(
                    claim.pk,
                    actor=request.user.email or request.user.get_username(),
                    reason=form.cleaned_data["reason"],
                    acknowledge_duplicate_risk=form.cleaned_data["acknowledge_duplicate_risk"],
                )
            except operations.ActionRefused as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Die Verarbeitung wurde erneut gestartet.")
                return redirect("admin:portal_claim_change", claim.pk)
        current = getattr(claim, "delivery", None)
        return TemplateResponse(
            request,
            "admin/portal/claim/retry.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": "Antrag erneut verarbeiten",
                "claim": claim,
                "status": claim_status(claim),
                "status_badge": claim_status_badge(claim),
                "duplicate_risk": bool(current and current.state != delivery.FAILED),
                "form": form,
            },
        )
