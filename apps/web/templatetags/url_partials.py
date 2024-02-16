from django import template

register = template.Library()


@register.simple_tag
def finalize_url(url, *args, placeholder="---"):
    """This tag is used to replace the placeholders in the url with the args passed to it.

    Usage:
    1. Create a url with placeholders:
        {% url "my_url" team "---" as the_url %}
    2. Use the tag to replace the placeholders:
        {% finalize_url the_url "last_param" %}

    For integer params use the "000" placeholder. For string params use the "---" placeholder.
    """
    for arg in args:
        url = url.replace(placeholder, str(arg), 1)
    return url
