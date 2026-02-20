from django import forms
from django.db import connection
from django.test import TestCase

from apps.service_providers.forms import ObfuscatingMixin
from apps.service_providers.models import LlmProvider, LlmProviderTypes
from apps.teams.models import Team


class TestLlmProviderModel(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="Test Team")

    def test_encryption(self):
        test_data = {"str": "str", "int": 1, "float": 2.0, "bool": True, "list": [1, "a", True, 3.5]}
        LlmProvider.objects.create(team=self.team, name="Test", type=LlmProviderTypes.openai, config=test_data)

        assert LlmProvider.objects.get(name="Test").config == test_data

        with connection.cursor() as cursor:
            cursor.execute("SELECT config FROM service_providers_llmprovider")
            assert cursor.fetchone()[0] != test_data  # ty: ignore[not-subscriptable]


class TestForm(ObfuscatingMixin, forms.Form):
    __test__ = False  # pytest ignore
    obfuscate_fields = ["field_a", "field_b"]

    field_a = forms.CharField()
    field_b = forms.CharField(required=False)
    field_c = forms.CharField()
