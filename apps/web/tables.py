from django.contrib.postgres.fields import ArrayField
from django_tables2 import TemplateColumn
from django_tables2.columns import library


@library.register
class ArrayColumn(TemplateColumn):
    def __init__(self, template_name=None, extra_context=None, **extra):
        template = '{{ value|join:", " }}'
        super().__init__(template_code=template, template_name=template_name, extra_context=extra_context, **extra)

    @classmethod
    def from_field(cls, field, **kwargs):
        if isinstance(field, ArrayField):
            return cls(**kwargs)
