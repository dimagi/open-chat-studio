import csv

from django.http import HttpResponse


def export_as_csv(modeladmin, request, queryset):
    meta = modeladmin.model._meta
    field_names = [field for field in modeladmin.list_display]

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = "attachment; filename={}.csv".format(meta)
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
