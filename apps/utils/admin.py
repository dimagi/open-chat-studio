class ReadonlyAdminMixin:
    """Marks every model field as readonly so FK/M2M widgets render as plain
    text instead of <select> dropdowns, avoiding full-table queries that can
    time out the admin change view. Merges with any readonly_fields the admin
    class already declares (e.g. computed display methods)."""

    def get_readonly_fields(self, request, obj=None):
        existing = list(super().get_readonly_fields(request, obj))
        model_fields = [field.name for field in self.opts.local_fields] + [
            field.name for field in self.opts.local_many_to_many
        ]
        return list(set(existing + model_fields))
