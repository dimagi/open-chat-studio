from rest_framework.pagination import CursorPagination as RestCursorPagination


class CursorPagination(RestCursorPagination):
    ordering = "-created_at"
    page_size_query_param = "page_size"
    max_page_size = 1500

    def paginate_queryset(self, queryset, request, view=None):
        # Compute the total count on the first page only (no cursor param).
        # Consumers syncing data (e.g. Scout) need the total once up front to
        # show real progress; skipping it on cursor-following requests keeps
        # deep pagination as cheap as plain cursor pagination.
        self.total_count = None
        if request.query_params.get(self.cursor_query_param) is None:
            self.total_count = self._get_count(queryset)
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        if self.total_count is not None:
            response.data["count"] = self.total_count
        return response

    def get_paginated_response_schema(self, schema):
        schema = super().get_paginated_response_schema(schema)
        schema["properties"]["count"] = {
            "type": "integer",
            "description": (
                "Total number of items matching the query. Only present on the first page (requests without a cursor)."
            ),
            "example": 123,
        }
        return schema

    @staticmethod
    def _get_count(queryset):
        try:
            return queryset.count()
        except (AttributeError, TypeError):
            return len(queryset)
