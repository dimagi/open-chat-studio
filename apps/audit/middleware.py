import uuid

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from field_audit.field_audit import request as audit_request

from apps.audit.transaction import audit_transaction


class AuditTransactionMiddleware:
    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        transaction_id = get_audit_transaction_id(request)
        with audit_transaction(transaction_id):
            return self.get_response(request)

    async def __acall__(self, request):
        transaction_id = get_audit_transaction_id(request)
        with audit_transaction(transaction_id):
            return await self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        audit_request.set(request)
        return None

    async def aprocess_view(self, request, view_func, view_args, view_kwargs):
        audit_request.set(request)
        return None


def get_audit_transaction_id(request):
    for header in settings.FIELD_AUDIT_REQUEST_ID_HEADERS:
        if transaction_id := request.headers.get(header):
            return transaction_id

    # generate one if none exist
    return uuid.uuid4().hex
