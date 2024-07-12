from django.contrib.contenttypes.models import ContentType

from apps.accounting.models import Usage, UsageType


def assert_usage(source, expected_usage: list[tuple[UsageType, int]]):
    content_type = ContentType.objects.get_for_model(source)
    actual = list(Usage.objects.filter(source_content_type=content_type).values_list("type", "value"))
    assert actual == expected_usage
