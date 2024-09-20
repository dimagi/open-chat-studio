from django.db.models import Model


def get_real_user_or_none(user):
    if user.is_anonymous:
        return None
    else:
        return user


def differs(original: any, new: any, exclude_model_fields: list[str] | None = None) -> bool:
    """
    Compares the value (or attributes in the case of a Model) between `original` and `new`.
    Returns `True` if it differs and `False` if not.

    When comparing models we only care about the fields that has business value, so anything except those in
    DEFAULT_EXCLUDED_KEYS
    """
    exclude_model_fields = exclude_model_fields or []
    if isinstance(original, Model) and isinstance(new, Model):
        return bool(compare_models(original, new, exclude_model_fields))
    return original != new


def compare_models(original: Model, new: Model, exclude_fields: list[str]) -> set:
    """
    Compares the field values of between `original` and `new`, excluding those in `DEFAULT_EXCLUDED_KEYS`.
    `expected_changed_fields` specifies what fields we expect there to be differences in
    """
    model_fields = [field.attname for field in original._meta.fields]
    original_dict, new_dict = original.__dict__, new.__dict__
    changed_fields = set([])
    for field_name, field_value in original_dict.items():
        if field_name not in model_fields:
            continue

        if field_name in exclude_fields:
            continue
        if field_value != new_dict[field_name]:
            changed_fields.add(field_name)

    return changed_fields
