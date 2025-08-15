from rest_framework.pagination import CursorPagination as RestCursorPagination


class CursorPagination(RestCursorPagination):
    ordering = "-created_at"
    page_size_query_param = "page_size"
    max_page_size = 1500
