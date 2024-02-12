import csv
import datetime

from dateutil import relativedelta
from django.contrib import admin
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _


class RelativeDateFieldListFilter(admin.DateFieldListFilter):
    def __init__(self, field, request, params, model, model_admin, field_path):
        super().__init__(field, request, params, model, model_admin, field_path)
        if field.null:
            self.links, _nulls = self.links[:-2], self.links[-2:]

        now = timezone.now()
        # When time zone support is enabled, convert "now" to the user's time
        # zone so Django's definition of "Today" matches what the user expects.
        if timezone.is_aware(now):
            now = timezone.localtime(now)

        if isinstance(field, models.DateTimeField):
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # field is a models.DateField
            today = now.date()

        tomorrow = today + datetime.timedelta(days=1)
        self.links = [
            (_("All"), {}),
        ]

        link_days = [
            (_("Past 7 days"), relativedelta.relativedelta(days=7)),
            (_("Past 14 days"), relativedelta.relativedelta(days=14)),
            (_("Past month"), relativedelta.relativedelta(months=1)),
            (_("Past 2 months"), relativedelta.relativedelta(months=2)),
            (_("Past 6 months"), relativedelta.relativedelta(months=6)),
            (_("Past year"), relativedelta.relativedelta(years=1)),
        ]
        for title, delta in link_days:
            self.links.append(
                (
                    title,
                    {
                        self.lookup_kwarg_since: str(today - delta),
                        self.lookup_kwarg_until: str(tomorrow),
                    },
                )
            )

        if field.null:
            self.links += (
                (_("No date"), {self.field_generic + "isnull": "True"}),
                (_("Has date"), {self.field_generic + "isnull": "False"}),
            )


def export_as_csv(modeladmin, request, queryset):
    meta = modeladmin.model._meta
    field_names = [field for field in modeladmin.list_display]

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f"attachment; filename={meta}.csv"
    writer = csv.writer(response)

    writer.writerow(field_names)
    for obj in queryset:
        row = []
        for field in field_names:
            if hasattr(obj, field):
                row.append(getattr(obj, field))
            elif hasattr(modeladmin, field):
                row.append(getattr(modeladmin, field)(obj))

        writer.writerow(row)

    return response
