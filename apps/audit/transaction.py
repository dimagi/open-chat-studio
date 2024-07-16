from contextlib import contextmanager
from contextvars import ContextVar

_audit_transaction = ContextVar("audit_transaction")


def get_audit_transaction_id():
    try:
        return _audit_transaction.get()
    except LookupError:
        pass


def set_audit_transaction_id(transaction_id: str):
    _audit_transaction.set(transaction_id)


def unset_audit_transaction_id():
    _audit_transaction.set(None)


@contextmanager
def audit_transaction(transaction_id: str):
    set_audit_transaction_id(transaction_id)
    try:
        yield
    finally:
        unset_audit_transaction_id()
