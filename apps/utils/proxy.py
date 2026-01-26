from typing import TypeVar

T = TypeVar("T")


class Proxy[T]:
    """Simple proxy object that forwards all attribute access to the target object."""

    def __init__(self, target: T) -> None:
        self.target: T = target

    def __getattr__(self, item):
        return getattr(self.target, item)
