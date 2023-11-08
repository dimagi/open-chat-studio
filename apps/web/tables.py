from django.contrib.postgres.fields import ArrayField
from django_tables2 import TemplateColumn
from django_tables2.columns import library


@library.register
class ArrayColumn(TemplateColumn):
    def __init__(self, *args, **kwargs):
        template = '{{ value|join:", " }}'
        super().__init__(template_code=template, *args, **kwargs)

    @classmethod
    def from_field(cls, field, **kwargs):
        if isinstance(field, ArrayField):
            return cls(**kwargs)
