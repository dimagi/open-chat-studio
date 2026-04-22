class ReadonlyAdminMixin:
    """Marks every model field as readonly on the change view so FK/M2M widgets
    render as plain text instead of <select> dropdowns, avoiding full-table
    queries that can time out the admin change view. On the add view (obj=None)
    only returns the admin's declared readonly_fields so required fields remain
    editable. Merges with any readonly_fields the admin class already declares
    (e.g. computed display methods), preserving their declared order."""

    def get_readonly_fields(self, request, obj=None):
        existing = list(super().get_readonly_fields(request, obj))
        if obj is None:
            return existing
        model_fields = [field.name for field in self.opts.local_fields] + [
            field.name for field in self.opts.local_many_to_many
        ]
        return existing + [field for field in model_fields if field not in existing]
