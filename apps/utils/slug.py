from django.utils.text import slugify


def get_next_unique_slug(model_class, input_value, field_name, model_instance=None):
    """
    Gets the next unique slug based on the name. Appends -1, -2, etc. until it finds
    a unique value.

    Args:
        model_class: The model class to check for uniqueness.
        input_value: The input value to generate the slug.
        field_name: The field name to check for uniqueness.
        model_instance: The model instance to exclude from the uniqueness check.
    """
    for next_value in next_slug_iterator(input_value):
        if not _instance_exists(model_class, field_name, next_value, model_instance):
            return next_value


def _instance_exists(model_class, field_name, field_value, model_instance=None):
    base_qs = model_class.objects.all()
    if model_instance and model_instance.pk:
        base_qs = base_qs.exclude(pk=model_instance.id)

    return base_qs.filter(**{f"{field_name}__exact": field_value}).exists()


def next_slug_iterator(display_name):
    base_slug = slugify(display_name)
    yield base_slug

    suffix = 2
    while True:
        yield get_next_slug(base_slug, suffix)
        suffix += 1


def get_next_slug(base_value, suffix, max_length=100):
    """
    Gets the next slug from base_value such that "base_value-suffix" will not exceed max_length characters.
    """
    suffix_length = len(str(suffix)) + 1  # + 1 for the "-" character
    if suffix_length >= max_length:
        raise ValueError(f"Suffix {suffix} is too long to create a unique slug! ")

    return f"{base_value[: max_length - suffix_length]}-{suffix}"
