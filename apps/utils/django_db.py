from django.db import models


class MakeInterval(models.Func):
    """A function to create an interval in Postgres
    See https://www.postgresql.org/docs/current/functions-datetime.html
    """

    function = "make_interval"
    arity = 2
    arg_joiner = " => "

    def __init__(self, unit, value):
        assert unit in ("years", "months", "weeks", "days", "hours", "mins", "secs")
        expressions = (UnquotedValue(unit), value)
        super().__init__(*expressions, output_field=models.DateTimeField())


class UnquotedValue(models.Value):
    """Raw value with no formatting (not even quotes)"""

    def as_sql(self, compiler, connection):  # ty: ignore[invalid-method-override]
        return self.value, []
