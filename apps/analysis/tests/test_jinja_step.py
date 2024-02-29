from apps.analysis.core import StepContext
from apps.analysis.steps.processors import JinjaTemplateParams, JinjaTemplateStep


def test_jinja_step():
    template = "# {{ data.foo }}{% for item in data.baz %}\n* {{ item }}{% endfor %}"
    context = StepContext(data={"foo": "bar", "baz": ["one", "two", "three"]})
    result = JinjaTemplateStep().run(JinjaTemplateParams(template=template), context)
    assert result.data == "# bar\n* one\n* two\n* three"
