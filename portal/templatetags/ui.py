"""Interface components, ported from shadcn-django (MIT) without django-cotton.

    {% load ui %}
    {% component "button" variant="outline" size="sm" type="submit" data_action="save" %}
      Speichern
    {% endcomponent %}

The body becomes `slot`. Keyword arguments that the component declares below are
template variables; `class` is appended to the component's classes; every other
argument becomes an HTML attribute (underscores turn into hyphens, `True` renders
`name="name"`, `False` and `None` drop it). Each component is `ui/<name>.html`.
"""

from django import template
from django.template.loader import get_template
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe

register = template.Library()

COMPONENTS = {
    "alert": {"variant": "default"},
    "alert_description": {},
    "alert_title": {},
    "badge": {"variant": "secondary"},
    "button": {"variant": "default", "size": "default", "href": None},
    "card": {},
    "card_content": {},
    "card_description": {},
    "card_footer": {},
    "card_header": {},
    "card_title": {},
    "kbd": {},
}


def html_attributes(values):
    """Attribute pairs for `format_html_join`; values are escaped there."""
    pairs = []
    for name, value in values.items():
        if value is None or value is False:
            continue
        name = name.replace("_", "-")
        pairs.append((name, name if value is True else value))
    return format_html_join("", ' {}="{}"', pairs)


class ComponentNode(template.Node):
    def __init__(self, name, options, nodelist):
        self.name, self.options, self.nodelist = name, options, nodelist

    def render(self, context):
        name = self.name.resolve(context)
        if name not in COMPONENTS:
            raise template.TemplateSyntaxError(f"Unknown component {name!r}.")
        values = {key: value.resolve(context) for key, value in self.options.items()}
        variables = {key: values.pop(key, default) for key, default in COMPONENTS[name].items()}
        variables["class"] = values.pop("class", "")
        variables["attrs"] = html_attributes(values)
        variables["slot"] = self.nodelist.render(context)
        return mark_safe(get_template(f"ui/{name}.html").render(variables).strip())


@register.tag
def component(parser, token):
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError("component needs a component name.")
    options = {}
    for bit in bits[2:]:
        key, separator, value = bit.partition("=")
        if not separator:
            raise template.TemplateSyntaxError(f"component expects key=value, got {bit!r}.")
        options[key] = parser.compile_filter(value)
    nodelist = parser.parse(("endcomponent",))
    parser.delete_first_token()
    return ComponentNode(parser.compile_filter(bits[1]), options, nodelist)
