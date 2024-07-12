def sane_repr(*attrs):
    """By default, Django calls __str__ in __repr__ which is sometimes
     not safe e.g. if the object is not completely populated.

    This utility can be used to provide safe __repr__ method for Django models.
    """
    if "id" not in attrs and "pk" not in attrs:
        attrs = ("id",) + attrs

    def _repr(self):
        cls = type(self).__name__

        pairs = (f"{a}={repr(getattr(self, a, None))}" for a in attrs)

        return "<{} at 0x{:x}: {}>".format(cls, id(self), ", ".join(pairs))

    return _repr
