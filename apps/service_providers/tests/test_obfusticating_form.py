from django import forms
from django.test import SimpleTestCase

from apps.service_providers.forms import ObfuscatingMixin


class TestForm(ObfuscatingMixin, forms.Form):
    obfuscate_fields = ["field_a", "field_b"]

    field_a = forms.CharField()
    field_b = forms.CharField(required=False)
    field_c = forms.CharField()


class TestObfuscatingForm(SimpleTestCase):
    def test_blank_form(self):
        form = TestForm()
        field_values = [f[0].value() for f in form.get_context()["fields"]]
        self.assertEqual(field_values, [None, None, None])

    def test_blank_form_save(self):
        form = TestForm(data={"field_a": "a" * 8, "field_b": "", "field_c": "c" * 8})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data, {"field_a": "a" * 8, "field_b": "", "field_c": "c" * 8})

    def test_initial(self):
        form = TestForm(initial={"field_a": "a" * 8, "field_b": "", "field_c": "c" * 8})
        field_values = [f[0].value() for f in form.get_context()["fields"]]
        self.assertEqual(field_values, ["aaaa****", "", "cccccccc"])

    def test_update_no_change(self):
        self._test_update("a" * 8, "b" * 8)

    def test_update_change(self):
        self._test_update("1" * 8, "2" * 8)

    def _test_update(self, field_a_new, field_b_new):
        form = TestForm(
            data={"field_a": field_a_new, "field_b": field_b_new, "field_c": "c" * 8},
            initial={"field_a": "a" * 8, "field_b": "b" * 8, "field_c": "c" * 8},
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data, {"field_a": field_a_new, "field_b": field_b_new, "field_c": "c" * 8})
