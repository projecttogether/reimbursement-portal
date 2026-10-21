import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from django.test import RequestFactory
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from portal import roles
from portal.auth import KeycloakBackend, keycloak_logout_url
from portal.models import Claim, Form, Job, Operation, PrivacyNotice

pytestmark = pytest.mark.django_db

ADMIN_GROUP = "/projecttogether/tools/reimbursement-portal-admins"
EDITOR_GROUP = "/projecttogether/tools/reimbursement-portal"


@pytest.fixture
def oidc(settings):
    settings.OIDC_RP_CLIENT_ID = "reimbursement-portal"
    settings.OIDC_RP_CLIENT_SECRET = "ci-only"
    settings.OIDC_ENABLED = True
    settings.KEYCLOAK_ADMIN_GROUP = ADMIN_GROUP
    settings.KEYCLOAK_EDITOR_GROUP = EDITOR_GROUP


def user_with(role, name):
    user = get_user_model().objects.create_user(name, email=f"{name}@example.invalid")
    roles.assign(user, {role})
    return user


@pytest.fixture
def admin_client(client):
    client.force_login(user_with(roles.TEMPLATE_ADMIN, "admin"))
    return client


@pytest.fixture
def editor(db):
    return user_with(roles.FORM_EDITOR, "editor")


def claims(oidc, **changes):
    return {
        "sub": "subject-1",
        "email": "person@projecttogether.org",
        "email_verified": True,
        "given_name": "Kim",
        "family_name": "Muster",
        "groups": [EDITOR_GROUP],
        **changes,
    }


def test_sign_in_maps_groups_to_roles_and_keys_users_by_subject(oidc):
    backend = KeycloakBackend()
    assert backend.verify_claims(claims(oidc))
    user = backend.create_user(claims(oidc))
    assert user.username == "subject-1" and user.is_staff
    assert roles.roles(user) == {roles.FORM_EDITOR}
    backend.update_user(user, claims(oidc, groups=[ADMIN_GROUP, "/other"]))
    assert roles.is_template_admin(user)
    assert list(backend.filter_users_by_claims(claims(oidc))) == [user]
    assert not backend.filter_users_by_claims(claims(oidc, sub="subject-2")).exists()


@pytest.mark.parametrize(
    "changes",
    [{"groups": ["/projecttogether/employees"]}, {"groups": None}, {"email_verified": False}],
)
def test_sign_in_requires_a_portal_group_and_verified_email(oidc, changes):
    assert not KeycloakBackend().verify_claims(claims(oidc, **changes))


def token(**changes):
    now = time.time()
    return {
        "iss": "https://auth.projecttogether.net/realms/projecttogether",
        "aud": "reimbursement-portal",
        "azp": "reimbursement-portal",
        "sub": "subject-1",
        "nonce": "nonce",
        "iat": now,
        "exp": now + 300,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://auth.example.invalid/realms/projecttogether"},
        {"aud": "another-client"},
        {"aud": ["reimbursement-portal", "another-client"]},
        {"azp": "another-client"},
        {"exp": time.time() - 1},
        {"iat": time.time() + 3600},
        {"nonce": ""},
    ],
)
def test_id_tokens_must_be_issued_for_this_client(oidc, monkeypatch, changes):
    monkeypatch.setattr(OIDCAuthenticationBackend, "verify_token", lambda *a, **k: token(**changes))
    with pytest.raises(SuspiciousOperation):
        KeycloakBackend().verify_token("signed")
    monkeypatch.setattr(OIDCAuthenticationBackend, "verify_token", lambda *a, **k: token())
    assert KeycloakBackend().verify_token("signed")["sub"] == "subject-1"


def test_admin_login_redirects_to_pt_login(client, oidc):
    response = client.get("/admin/login/?next=/admin/portal/form/")
    assert response.status_code == 302
    assert response.url == "/oidc/authenticate/?next=%2Fadmin%2Fportal%2Fform%2F"


def test_logout_ends_the_keycloak_session(oidc, settings):
    request = RequestFactory().get("/")
    request.session = {"oidc_id_token": "id-token"}
    url = urlsplit(keycloak_logout_url(request))
    assert url.path.endswith("/realms/projecttogether/protocol/openid-connect/logout")
    query = parse_qs(url.query)
    assert query["id_token_hint"] == ["id-token"]
    assert query["client_id"] == ["reimbursement-portal"]


def test_users_without_a_role_cannot_use_the_admin(client):
    client.force_login(get_user_model().objects.create_user("nobody"))
    assert client.get("/admin/").status_code == 302


def test_admin_creates_a_form_from_a_template(admin_client, template, settings):
    assert admin_client.get("/admin/portal/form/add/").status_code == 200
    url = f"/admin/portal/form/add/?template={template.pk}"
    page = admin_client.get(url).content.decode()
    assert "Kostenstelle (KST)" in page and "finance@example.invalid" in page
    data = {
        "name": "Werkstatt Herbst",
        "slug": "werkstatt-herbst",
        "value_kst": "",
        "value_ktr": "4711",
        "recipient": "finance@example.invalid",
        "support_email": "team@example.invalid",
        "purpose_options": "Teilnahme\nMitwirkung",
        "active": "on",
    }
    response = admin_client.post(url, data)
    assert response.status_code == 200 and not Form.objects.exists()
    data["value_kst"] = "1234"
    response = admin_client.post(url, data)
    assert response.status_code == 302, response.content
    form = Form.objects.get()
    assert form.template == template
    assert form.field_values == {"kst": "1234", "ktr": "4711"}
    assert form.owners.get().username == "admin"
    assert form.internal_values() == [
        ("Kostenstelle (KST)", "1234"),
        ("Kostenträger (KTR)", "4711"),
    ]


def test_unapproved_recipients_cannot_be_chosen(admin_client, template):
    url = f"/admin/portal/form/add/?template={template.pk}"
    response = admin_client.post(
        url,
        {
            "name": "Werkstatt",
            "slug": "werkstatt",
            "value_kst": "1234",
            "recipient": "attacker@example.invalid",
            "support_email": "team@example.invalid",
        },
    )
    assert response.status_code == 200 and not Form.objects.exists()


def test_editors_see_only_their_forms_and_claims(client, editor, accepted):
    other = Form.objects.create(
        template=accepted.form.template,
        slug="other",
        name="Andere Werkstatt",
        recipient="finance@example.invalid",
        support_email="finance@example.invalid",
    )
    other.owners.add(editor)
    client.force_login(editor)
    forms = client.get("/admin/portal/form/").content.decode()
    assert "Andere Werkstatt" in forms and "Test Workshop" not in forms
    assert client.get(f"/admin/portal/form/{accepted.form.pk}/change/").status_code == 302
    assert "Alex Beispiel" not in client.get("/admin/portal/claim/").content.decode()
    assert client.get(f"/admin/portal/claim/{accepted.pk}/change/").status_code == 302
    accepted.form.owners.add(editor)
    page = client.get("/admin/portal/claim/").content.decode()
    assert "Alex Beispiel" in page and "participant@example.invalid" in page
    assert "DE89" not in page
    detail = client.get(f"/admin/portal/claim/{accepted.pk}/change/").content.decode()
    assert "Alex Beispiel" in detail and "Erneut verarbeiten" not in detail
    assert client.get(f"/admin/portal/claim/{accepted.pk}/erneut-versuchen/").status_code == 403


def test_editors_cannot_change_templates_or_the_privacy_notice(client, editor, template):
    client.force_login(editor)
    assert client.get(f"/admin/portal/formtemplate/{template.pk}/change/").status_code == 200
    response = client.post(f"/admin/portal/formtemplate/{template.pk}/change/", {"name": "X"})
    assert response.status_code == 403
    assert client.get("/admin/portal/privacynotice/").status_code == 403
    assert client.get("/admin/portal/formtemplate/add/").status_code == 403


def test_admin_adds_a_new_privacy_notice_version(admin_client, privacy_notice):
    page = admin_client.get("/admin/portal/privacynotice/add/").content.decode()
    assert privacy_notice.text.splitlines()[0][:20] in page
    response = admin_client.post("/admin/portal/privacynotice/add/", {"text": "Neue Fassung."})
    assert response.status_code == 302
    current = PrivacyNotice.current()
    assert current.text == "Neue Fassung." and current.created_by == "admin@example.invalid"
    assert admin_client.post(
        f"/admin/portal/privacynotice/{current.pk}/change/", {"text": "Edit"}
    ).status_code in {403, 302}
    assert PrivacyNotice.current().text == "Neue Fassung."


def test_admin_retries_a_failed_render_with_an_audited_reason(admin_client, accepted):
    Claim.objects.filter(pk=accepted.pk).update(state=Claim.State.FAILED, error_code="timeout")
    Job.objects.all().delete()
    url = f"/admin/portal/claim/{accepted.pk}/erneut-versuchen/"
    assert "Erneut verarbeiten" in admin_client.get(url).content.decode()
    assert admin_client.post(url, {"reason": ""}).status_code == 200
    response = admin_client.post(url, {"reason": "Sandbox repariert"})
    assert response.status_code == 302
    accepted.refresh_from_db()
    assert accepted.state == Claim.State.RECEIVED
    operation = Operation.objects.get()
    assert (operation.actor, operation.reason) == ("admin@example.invalid", "Sandbox repariert")


def test_admin_resend_requires_duplicate_risk_acknowledgment(admin_client, delivery):
    delivery.state = "delivered"
    delivery.save()
    url = f"/admin/portal/claim/{delivery.claim_id}/erneut-versuchen/"
    response = admin_client.post(url, {"reason": "Moss hat nichts erhalten"})
    assert response.status_code == 200 and "doppelten Versands" in response.content.decode()
    response = admin_client.post(
        url, {"reason": "Moss hat nichts erhalten", "acknowledge_duplicate_risk": "on"}
    )
    assert response.status_code == 302
    delivery.refresh_from_db()
    assert delivery.state == "pending"


def test_claim_search_finds_a_full_reference(admin_client, accepted):
    page = admin_client.get(f"/admin/portal/claim/?q={accepted.reference}").content.decode()
    assert "Alex Beispiel" in page
    page = admin_client.get(f"/admin/portal/claim/?q={accepted.reference[:8]}").content.decode()
    assert "Alex Beispiel" not in page
