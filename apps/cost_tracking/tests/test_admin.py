"""Guards for the UsageRecord admin's read-only contract."""

from django.contrib.admin.sites import AdminSite

from apps.cost_tracking.admin import UsageRecordAdmin
from apps.cost_tracking.models import UsageRecord


def test_usage_record_change_form_has_nothing_writable():
    """UsageRecord is system-written audit data, and `source` in particular decides
    whether a row counts toward per-entity cost — flipping it by hand would silently
    reclassify spend. Adding a field without listing it in `readonly_fields` reopens
    that hole, so assert the set is exhaustive rather than trusting the next author
    to remember.
    """
    model_admin = UsageRecordAdmin(UsageRecord, AdminSite())
    # `formfield() is not None` rather than `.editable`: AutoField reports itself as
    # editable but returns no form field, so the pk would be a false positive.
    formable = {
        field.name for field in UsageRecord._meta.concrete_fields if field.editable and field.formfield() is not None
    }

    assert formable - set(model_admin.readonly_fields) == set()
