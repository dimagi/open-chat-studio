from django.db import connection
from django.test import TestCase

from apps.llm_providers.models import LlmProvider, LlmProviderType
from apps.teams.models import Team


class TestLlmProviderModel(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="Test Team")

    def test_encryption(self):
        test_data = {"str": "str", "int": 1, "float": 2.0, "bool": True, "list": [1, "a", True, 3.5]}
        LlmProvider.objects.create(team=self.team, name="Test", type=LlmProviderType.openai, config=test_data)

        assert LlmProvider.objects.get(name="Test").config == test_data

        with connection.cursor() as cursor:
            cursor.execute("SELECT config FROM llm_providers_llmprovider")
            assert cursor.fetchone()[0] != test_data
