"""Tests for custom Django model fields."""

import pytest


@pytest.mark.django_db()
class TestSanitizedJSONField:
    """Test the SanitizedJSONField custom field."""

    @pytest.fixture(autouse=True)
    def setup_test_model(self):
        """Create a temporary test model for testing the field."""
        # We'll test using the existing EvaluationResult model since it uses SanitizedJSONField
        from apps.evaluations.models import EvaluationResult

        self.model = EvaluationResult

    def test_sanitize_null_bytes(self, team_with_users):
        """Test that null bytes are removed from strings."""
        from apps.evaluations.models import EvaluationMessage, EvaluationRun, Evaluator
        from apps.utils.factories.evaluations import EvaluationConfigFactory

        # Create necessary objects
        config = EvaluationConfigFactory(team=team_with_users)
        run = EvaluationRun.objects.create(team=team_with_users, config=config)
        message = EvaluationMessage.objects.create()
        evaluator = Evaluator.objects.create(team=team_with_users, name="Test", type="TestEvaluator")

        # Create a result with null bytes in the output
        data_with_null_bytes = {
            "result": {"score": "test\x00value", "comment": "another\x00null\x00byte"},
            "metadata": {"key": "value\x00with\x00nulls"},
        }

        result = self.model.objects.create(
            team=team_with_users,
            evaluator=evaluator,
            message=message,
            run=run,
            output=data_with_null_bytes,
        )

        # Refresh from database to get the sanitized value
        result.refresh_from_db()

        # Verify null bytes are removed
        assert "\x00" not in result.output["result"]["score"]
        assert result.output["result"]["score"] == "testvalue"
        assert "\x00" not in result.output["result"]["comment"]
        assert result.output["result"]["comment"] == "anothernullbyte"
        assert "\x00" not in result.output["metadata"]["key"]
        assert result.output["metadata"]["key"] == "valuewithnulls"

    def test_sanitize_control_characters(self, team_with_users):
        """Test that control characters (except whitespace) are removed."""
        from apps.evaluations.models import EvaluationMessage, EvaluationRun, Evaluator
        from apps.utils.factories.evaluations import EvaluationConfigFactory

        config = EvaluationConfigFactory(team=team_with_users)
        run = EvaluationRun.objects.create(team=team_with_users, config=config)
        message = EvaluationMessage.objects.create()
        evaluator = Evaluator.objects.create(team=team_with_users, name="Test", type="TestEvaluator")

        # Create data with various control characters
        data_with_control_chars = {
            "result": {
                "text": "test\x01\x02\x03value",  # Control chars 0x01-0x03
                "multiline": "line1\nline2\rline3\tindented",  # Valid whitespace should remain
            }
        }

        result = self.model.objects.create(
            team=team_with_users,
            evaluator=evaluator,
            message=message,
            run=run,
            output=data_with_control_chars,
        )

        result.refresh_from_db()

        # Control characters should be removed
        assert "\x01" not in result.output["result"]["text"]
        assert "\x02" not in result.output["result"]["text"]
        assert "\x03" not in result.output["result"]["text"]
        assert result.output["result"]["text"] == "testvalue"

        # But valid whitespace (\n, \r, \t) should be preserved
        assert "\n" in result.output["result"]["multiline"]
        assert "\r" in result.output["result"]["multiline"]
        assert "\t" in result.output["result"]["multiline"]
        assert result.output["result"]["multiline"] == "line1\nline2\rline3\tindented"

    def test_sanitize_nested_structures(self, team_with_users):
        """Test that sanitization works on nested dicts and lists."""
        from apps.evaluations.models import EvaluationMessage, EvaluationRun, Evaluator
        from apps.utils.factories.evaluations import EvaluationConfigFactory

        config = EvaluationConfigFactory(team=team_with_users)
        run = EvaluationRun.objects.create(team=team_with_users, config=config)
        message = EvaluationMessage.objects.create()
        evaluator = Evaluator.objects.create(team=team_with_users, name="Test", type="TestEvaluator")

        # Create nested data with null bytes
        nested_data = {
            "result": {
                "nested": {"deep": {"value": "test\x00null"}},
                "list": ["item1\x00", "item2\x00", {"key": "value\x00"}],
            }
        }

        result = self.model.objects.create(
            team=team_with_users,
            evaluator=evaluator,
            message=message,
            run=run,
            output=nested_data,
        )

        result.refresh_from_db()

        # Verify nested values are sanitized
        assert result.output["result"]["nested"]["deep"]["value"] == "testnull"
        assert result.output["result"]["list"][0] == "item1"
        assert result.output["result"]["list"][1] == "item2"
        assert result.output["result"]["list"][2]["key"] == "value"

    def test_primitives_unchanged(self, team_with_users):
        """Test that non-string primitives are unchanged."""
        from apps.evaluations.models import EvaluationMessage, EvaluationRun, Evaluator
        from apps.utils.factories.evaluations import EvaluationConfigFactory

        config = EvaluationConfigFactory(team=team_with_users)
        run = EvaluationRun.objects.create(team=team_with_users, config=config)
        message = EvaluationMessage.objects.create()
        evaluator = Evaluator.objects.create(team=team_with_users, name="Test", type="TestEvaluator")

        # Create data with various primitive types
        data_with_primitives = {
            "result": {
                "integer": 42,
                "float": 3.14,
                "boolean": True,
                "null_value": None,
                "string": "normal string",
            }
        }

        result = self.model.objects.create(
            team=team_with_users,
            evaluator=evaluator,
            message=message,
            run=run,
            output=data_with_primitives,
        )

        result.refresh_from_db()

        # Verify primitives are unchanged
        assert result.output["result"]["integer"] == 42
        assert result.output["result"]["float"] == 3.14
        assert result.output["result"]["boolean"] is True
        assert result.output["result"]["null_value"] is None
        assert result.output["result"]["string"] == "normal string"
