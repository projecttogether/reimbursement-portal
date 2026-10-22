"""Admin roles. PT Login groups map to these Django groups at every sign-in.

Template admins manage templates, the privacy notice, every form and claim actions.
Form editors create forms from templates and see the claims of the forms they own.
"""

from django.contrib.auth.models import Group

TEMPLATE_ADMIN = "Vorlagen-Admins"
FORM_EDITOR = "Formular-Editoren"
ROLES = (TEMPLATE_ADMIN, FORM_EDITOR)


def roles(user):
    if not user.is_authenticated or not user.is_active:
        return frozenset()
    cached = getattr(user, "_portal_roles", None)
    if cached is None:
        cached = frozenset(user.groups.filter(name__in=ROLES).values_list("name", flat=True))
        if user.is_superuser:
            cached |= {TEMPLATE_ADMIN}
        user._portal_roles = cached
    return cached


def is_template_admin(user):
    return TEMPLATE_ADMIN in roles(user)


def is_form_editor(user):
    """Form editing is part of both roles."""
    return bool(roles(user))


def assign(user, granted):
    """Replace the user's portal roles; staff access follows from having any role."""
    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLES}
    user.groups.remove(*groups.values())
    user.groups.add(*(groups[name] for name in granted))
    user.is_staff = bool(granted)
    user.save(update_fields=["is_staff"])
    user._portal_roles = None
