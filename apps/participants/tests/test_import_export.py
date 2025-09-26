import csv
import io
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.http import HttpResponse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant, ParticipantData
from apps.participants.import_export import export_participant_data_to_response, process_participant_import


@pytest.fixture()
def participants(team_with_users):
    """Create test participants"""
    return [
        Participant.objects.create(
            team=team_with_users, platform=ChannelPlatform.WEB.value, identifier="user1@example.com", name="User One"
        ),
        Participant.objects.create(
            team=team_with_users,
            platform=ChannelPlatform.TELEGRAM.value,
            identifier="user2@example.com",
            name="User Two",
        ),
    ]


@pytest.fixture()
def participant_data_records(participants, experiment):
    """Create test participant data records"""
    return [
        ParticipantData.objects.create(
            team=experiment.team,
            participant=participants[0],
            experiment=experiment,
            data={"age": 25, "city": "New York", "name": "User One"},
        ),
        ParticipantData.objects.create(
            team=experiment.team,
            participant=participants[1],
            experiment=experiment,
            data={"age": 30, "city": "Los Angeles", "name": "User Two"},
        ),
    ]


@pytest.fixture()
def simple_csv_content():
    """CSV content for basic import test"""
    return """identifier,channel,name
user1@example.com,web,User One
user2@example.com,telegram,User Two"""


@pytest.fixture()
def csv_with_data_content():
    """CSV content with participant data fields"""
    return """identifier,channel,name,data.age,data.city,data.preferences
user1@example.com,web,User One,25,New York,"{""theme"": ""dark""}"
user2@example.com,telegram,User Two,30,Los Angeles,"{""notifications"": true}"""


@pytest.fixture()
def invalid_csv_content():
    """CSV content with validation errors"""
    return """identifier,channel,name
,web,Missing Identifier
user@example.com,,Missing Platform  
user@example.com,invalid_platform,Invalid Platform"""


@pytest.mark.django_db()
class TestProcessParticipantImport:
    def test_import_basic_participants(self, team_with_users, simple_csv_content):
        """Test importing basic participant data without experiment data"""
        csv_file = io.BytesIO(simple_csv_content.encode("utf-8"))

        result = process_participant_import(csv_file, None, team_with_users)

        assert result["created"] == 2
        assert result["updated"] == 0
        assert result["errors"] == []

        # Verify participants were created
        participants = Participant.objects.filter(team=team_with_users)
        assert participants.count() == 2

        user1 = participants.get(identifier="user1@example.com")
        assert user1.platform == ChannelPlatform.WEB.value
        assert user1.name == "User One"

        user2 = participants.get(identifier="user2@example.com")
        assert user2.platform == ChannelPlatform.TELEGRAM.value
        assert user2.name == "User Two"

    def test_import_participants_with_data(self, team_with_users, experiment, csv_with_data_content):
        """Test importing participants with data fields"""
        csv_file = io.BytesIO(csv_with_data_content.encode("utf-8"))

        result = process_participant_import(csv_file, experiment, team_with_users)

        assert result["created"] == 2
        assert result["updated"] == 0
        assert result["errors"] == []

        # Verify participant data was created
        user1 = Participant.objects.get(team=team_with_users, identifier="user1@example.com")
        user1_data = ParticipantData.objects.get(participant=user1, experiment=experiment)

        expected_data = {"age": 25, "city": "New York", "preferences": {"theme": "dark"}, "name": "User One"}
        assert user1_data.data == expected_data

        user2 = Participant.objects.get(team=team_with_users, identifier="user2@example.com")
        user2_data = ParticipantData.objects.get(participant=user2, experiment=experiment)

        expected_data = {"age": 30, "city": "Los Angeles", "preferences": {"notifications": True}, "name": "User Two"}
        assert user2_data.data == expected_data

    def test_update_existing_participants(self, team_with_users, participants):
        """Test updating existing participants"""
        csv_content = """identifier,channel,name
user1@example.com,web,Updated User One
user2@example.com,telegram,Updated User Two"""

        csv_file = io.BytesIO(csv_content.encode("utf-8"))

        result = process_participant_import(csv_file, None, team_with_users)

        assert result["created"] == 0
        assert result["updated"] == 2
        assert result["errors"] == []

        # Verify names were updated
        user1 = Participant.objects.get(team=team_with_users, identifier="user1@example.com")
        assert user1.name == "Updated User One"

        user2 = Participant.objects.get(team=team_with_users, identifier="user2@example.com")
        assert user2.name == "Updated User Two"

    def test_merge_participant_data(self, team_with_users, experiment, participant_data_records):
        """Test merging data with existing participant data records"""
        csv_content = """identifier,channel,name,data.age,data.country
user1@example.com,web,User One,26,USA"""

        csv_file = io.BytesIO(csv_content.encode("utf-8"))

        result = process_participant_import(csv_file, experiment, team_with_users)

        assert result["created"] == 0
        assert result["updated"] == 1
        assert result["errors"] == []

        # Verify data was merged (old data preserved, new data added/updated)
        user1 = Participant.objects.get(team=team_with_users, identifier="user1@example.com")
        user1_data = ParticipantData.objects.get(participant=user1, experiment=experiment)

        expected_data = {
            "age": 26,  # updated
            "city": "New York",  # preserved
            "country": "USA",  # added
            "name": "User One",  # added
        }
        assert user1_data.data == expected_data

    def test_validation_errors(self, team_with_users, invalid_csv_content):
        """Test validation errors are properly reported"""
        csv_file = io.BytesIO(invalid_csv_content.encode("utf-8"))

        result = process_participant_import(csv_file, None, team_with_users)

        assert result["created"] == 0
        assert result["updated"] == 0
        assert len(result["errors"]) == 3

        errors = result["errors"]
        assert "Row 2: identifier is required" in errors
        assert "Row 3: channel is required" in errors
        assert "Row 4: invalid channel 'invalid_platform'" in errors[2]

    def test_data_without_experiment_error(self, team_with_users):
        """Test error when trying to import data fields without experiment"""
        csv_content = """identifier,channel,name,data.age
user1@example.com,web,User One,25"""

        csv_file = io.BytesIO(csv_content.encode("utf-8"))

        result = process_participant_import(csv_file, None, team_with_users)

        assert result["created"] == 0
        assert result["updated"] == 0
        assert len(result["errors"]) == 1
        assert "Row 2: participant data import requires a chatbot" in result["errors"][0]

    def test_json_parsing_in_data_fields(self, team_with_users, experiment):
        """Test JSON parsing and fallback to string for data fields"""
        csv_content = """identifier,channel,name,data.json_field,data.string_field
user1@example.com,web,User One,"{""key"": ""value""}","just a string"
user2@example.com,web,User Two,"invalid {json",another string"""

        csv_file = io.BytesIO(csv_content.encode("utf-8"))

        result = process_participant_import(csv_file, experiment, team_with_users)

        assert result["created"] == 2
        assert result["errors"] == []

        # Check JSON was parsed correctly
        user1 = Participant.objects.get(team=team_with_users, identifier="user1@example.com")
        user1_data = ParticipantData.objects.get(participant=user1, experiment=experiment)
        assert user1_data.data["json_field"] == {"key": "value"}
        assert user1_data.data["string_field"] == "just a string"

        # Check invalid JSON fell back to string
        user2 = Participant.objects.get(team=team_with_users, identifier="user2@example.com")
        user2_data = ParticipantData.objects.get(participant=user2, experiment=experiment)
        assert user2_data.data["json_field"] == "invalid {json"
        assert user2_data.data["string_field"] == "another string"

    def test_empty_csv(self, team_with_users):
        """Test handling empty CSV file"""
        csv_file = io.BytesIO(b"identifier,channel,name\n")

        result = process_participant_import(csv_file, None, team_with_users)

        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["errors"] == []

    def test_exception_handling(self, team_with_users):
        """Test that exceptions in processing are caught and reported"""
        csv_content = """identifier,channel,name
user@example.com,web,User"""

        csv_file = io.BytesIO(csv_content.encode("utf-8"))

        # Mock Participant.objects.get_or_create to raise an exception
        with patch("apps.experiments.models.Participant.objects.get_or_create") as mock_create:
            mock_create.side_effect = IntegrityError("Database error")

            result = process_participant_import(csv_file, None, team_with_users)

        assert result["created"] == 0
        assert result["updated"] == 0
        assert len(result["errors"]) == 1
        assert "Row 2: Database error" in result["errors"][0]


@pytest.mark.django_db()
class TestExportParticipantDataToResponse:
    def test_export_basic_participants(self, team_with_users, participants):
        """Test exporting basic participants without experiment data"""
        queryset = Participant.objects.filter(team=team_with_users)

        response = export_participant_data_to_response(team_with_users, None, queryset)

        assert isinstance(response, HttpResponse)
        assert response["Content-Type"] == "text/csv"
        assert f"participants_{team_with_users.slug}.csv" in response["Content-Disposition"]

        # Parse CSV content
        content = response.content.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(content))
        rows = list(csv_reader)

        assert len(rows) == 2

        # Verify header
        expected_headers = ["identifier", "channel", "name"]
        assert list(csv_reader.fieldnames) == expected_headers

        # Verify data (ordered by channel, identifier)
        assert rows[0]["identifier"] == "user2@example.com"  # telegram comes before web
        assert rows[0]["channel"] == "telegram"
        assert rows[0]["name"] == "User Two"

        assert rows[1]["identifier"] == "user1@example.com"
        assert rows[1]["channel"] == "web"
        assert rows[1]["name"] == "User One"

    def test_export_with_experiment_data(self, team_with_users, experiment, participants, participant_data_records):
        """Test exporting participants with experiment data"""
        queryset = Participant.objects.filter(team=team_with_users)

        response = export_participant_data_to_response(team_with_users, experiment, queryset)
        assert isinstance(response, HttpResponse)

        # Parse CSV content
        content = response.content.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(content))
        rows = list(csv_reader)

        assert len(rows) == 2

        # Verify headers include data fields
        expected_headers = ["identifier", "channel", "name", "data.age", "data.city", "data.name"]
        assert set(csv_reader.fieldnames) == set(expected_headers)

        # Verify data content
        web_row = next(row for row in rows if row["channel"] == "web")
        assert web_row["identifier"] == "user1@example.com"
        assert web_row["name"] == "User One"
        assert web_row["data.age"] == "25"
        assert web_row["data.city"] == "New York"

        telegram_row = next(row for row in rows if row["channel"] == "telegram")
        assert telegram_row["identifier"] == "user2@example.com"
        assert telegram_row["name"] == "User Two"
        assert telegram_row["data.age"] == "30"
        assert telegram_row["data.city"] == "Los Angeles"

    def test_export_participants_missing_data(self, team_with_users, experiment, participants):
        """Test exporting participants where some have no data for the experiment"""
        # Create data for only one participant
        ParticipantData.objects.create(
            team=team_with_users,
            participant=participants[0],
            experiment=experiment,
            data={"age": 25, "city": "New York"},
        )

        queryset = Participant.objects.filter(team=team_with_users)
        response = export_participant_data_to_response(team_with_users, experiment, queryset)

        # Parse CSV content
        content = response.content.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(content))
        rows = list(csv_reader)

        assert len(rows) == 2

        # Participant with data should have values
        web_row = next(row for row in rows if row["channel"] == "web")
        assert web_row["data.age"] == "25"
        assert web_row["data.city"] == "New York"

        # Participant without data should have empty strings
        telegram_row = next(row for row in rows if row["channel"] == "telegram")
        assert telegram_row["data.age"] == ""
        assert telegram_row["data.city"] == ""

    def test_export_empty_queryset(self, team_with_users):
        """Test exporting empty participant queryset"""
        queryset = Participant.objects.none()

        response = export_participant_data_to_response(team_with_users, None, queryset)

        content = response.content.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(content))
        rows = list(csv_reader)

        assert len(rows) == 0
        assert list(csv_reader.fieldnames) == ["identifier", "channel", "name"]

    def test_export_complex_data_types(self, team_with_users, experiment, participants):
        """Test exporting participants with complex data types (dicts, lists)"""
        ParticipantData.objects.create(
            team=team_with_users,
            participant=participants[0],
            experiment=experiment,
            data={
                "preferences": {"theme": "dark", "notifications": True},
                "tags": ["vip", "premium"],
                "metadata": {"source": "web", "score": 85.5},
            },
        )

        queryset = Participant.objects.filter(team=team_with_users)
        response = export_participant_data_to_response(team_with_users, experiment, queryset)

        # Parse CSV content
        content = response.content.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(content))
        rows = list(csv_reader)

        web_row = next(row for row in rows if row["channel"] == "web")

        # Complex data types should be exported as their string representation
        assert "data.preferences" in csv_reader.fieldnames
        assert "data.tags" in csv_reader.fieldnames
        assert "data.metadata" in csv_reader.fieldnames

        # Values should be present (exact format may vary)
        assert web_row["data.preferences"] != '{"theme": "dark", "notifications": true}'
        assert web_row["data.tags"] != '["vip", "premium"]'
        assert web_row["data.metadata"] != '{"source": "web", "score": 85.5}'

    def test_filename_generation(self, team_with_users, experiment):
        """Test CSV filename generation"""
        queryset = Participant.objects.none()

        # Test without experiment
        response = export_participant_data_to_response(team_with_users, None, queryset)
        expected = f"participants_{team_with_users.slug}.csv"
        assert expected in response["Content-Disposition"]

        # Test with experiment
        response = export_participant_data_to_response(team_with_users, experiment, queryset)
        expected = f"participants_{team_with_users.slug}_{experiment.name}.csv"
        assert expected in response["Content-Disposition"]
