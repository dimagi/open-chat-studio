from datetime import UTC, datetime

import pytest
from time_machine import travel

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.filters import ChatMessageFilter, MessageVersionsFilter
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams


@pytest.fixture()
def team_with_messages(db, team):
    """Create team with chat messages that have tags and versions."""
    # Create tags
    tag1 = Tag.objects.create(team=team, name="important")
    tag2 = Tag.objects.create(team=team, name="urgent")
    version_tag = Tag.objects.create(team=team, name="v1.0", category=TagCategories.EXPERIMENT_VERSION)

    chat = ChatFactory(team=team)

    with travel(datetime(2024, 1, 1, tzinfo=UTC)):
        msg1 = ChatMessageFactory(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content="Important message",
        )
        msg1.add_tag(tag1, team=team)
        msg1.add_tag(version_tag, team=team)

    with travel(datetime(2024, 1, 15, tzinfo=UTC)):
        msg2 = ChatMessageFactory(
            chat=chat,
            message_type=ChatMessageType.AI,
            content="Urgent response",
        )
        msg2.add_tag(tag2, team=team)
        msg2.add_tag(version_tag, team=team)

    with travel(datetime(2024, 2, 1, tzinfo=UTC)):
        msg3 = ChatMessageFactory(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content="Regular message",
        )

    return {
        "team": team,
        "chat": chat,
        "messages": [msg1, msg2, msg3],
        "tags": [tag1, tag2, version_tag],
        "important_msg": msg1,
        "urgent_msg": msg2,
        "regular_msg": msg3,
    }


@pytest.mark.django_db()
class TestMessageVersionsFilter:
    """Test the message-specific versions filter."""

    def test_apply_any_of(self, team_with_messages):
        """Test filtering messages by version tags."""
        filter_instance = MessageVersionsFilter()

        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Filter for version v1.0
        filtered = filter_instance.apply_any_of(queryset, ["v1.0"])

        # Should return messages with version tag
        assert filtered.count() == 2
        assert team_with_messages["important_msg"] in filtered
        assert team_with_messages["urgent_msg"] in filtered
        assert team_with_messages["regular_msg"] not in filtered

    def test_apply_excludes(self, team_with_messages):
        """Test excluding messages by version tags."""
        filter_instance = MessageVersionsFilter()

        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Exclude version v1.0
        filtered = filter_instance.apply_excludes(queryset, ["v1.0"])

        # Should return only messages without version tag
        assert filtered.count() == 1
        assert team_with_messages["regular_msg"] in filtered
        assert team_with_messages["important_msg"] not in filtered
        assert team_with_messages["urgent_msg"] not in filtered


@pytest.mark.django_db()
class TestChatMessageFilter:
    """Test the complete ChatMessageFilter."""

    def test_filter_by_tags(self, team_with_messages):
        """Test filtering messages by tags."""
        message_filter = ChatMessageFilter()
        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Create filter params for tags
        filter_params = FilterParams(
            column_filters=[ColumnFilterData(column="tags", operator="any of", value='["important"]')]
        )

        filtered = message_filter.apply(queryset, filter_params)

        assert filtered.count() == 1
        assert team_with_messages["important_msg"] in filtered
        assert team_with_messages["urgent_msg"] not in filtered
        assert team_with_messages["regular_msg"] not in filtered

    def test_filter_by_timestamp_range(self, team_with_messages):
        """Test filtering messages by timestamp range using relative time."""
        message_filter = ChatMessageFilter()
        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Calculate days from the second message (Jan 15, 2024) to create a range that excludes the first message
        second_msg_date = team_with_messages["urgent_msg"].created_at
        today = datetime.now(UTC)
        days_since_second_msg = (today - second_msg_date).days
        range_days = days_since_second_msg + 1  # Just past the second message date

        filter_params = FilterParams(
            column_filters=[ColumnFilterData(column="last_message", operator="range", value=f"{range_days}d")]
        )

        filtered = message_filter.apply(queryset, filter_params)

        # Should include only the two newer messages (Jan 15 and Feb 1), excluding Jan 1
        assert filtered.count() == 2
        assert team_with_messages["urgent_msg"] in filtered  # Jan 15, 2024 - in range
        assert team_with_messages["regular_msg"] in filtered  # Feb 1, 2024 - in range
        assert team_with_messages["important_msg"] not in filtered  # Jan 1, 2024 - excluded

    def test_filter_by_timestamp_after(self, team_with_messages):
        """Test filtering messages after a specific date."""
        message_filter = ChatMessageFilter()
        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Filter for messages after Jan 10, 2024
        filter_params = FilterParams(
            column_filters=[ColumnFilterData(column="last_message", operator="after", value="2024-01-10")]
        )

        filtered = message_filter.apply(queryset, filter_params)

        # Should include messages from Jan 15 and Feb 1
        assert filtered.count() == 2
        assert team_with_messages["urgent_msg"] in filtered
        assert team_with_messages["regular_msg"] in filtered
        assert team_with_messages["important_msg"] not in filtered

    def test_filter_by_versions(self, team_with_messages):
        """Test filtering messages by versions."""
        message_filter = ChatMessageFilter()
        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Create filter params for versions
        filter_params = FilterParams(
            column_filters=[ColumnFilterData(column="versions", operator="any of", value='["v1.0"]')]
        )

        filtered = message_filter.apply(queryset, filter_params)

        assert filtered.count() == 2
        assert team_with_messages["important_msg"] in filtered
        assert team_with_messages["urgent_msg"] in filtered
        assert team_with_messages["regular_msg"] not in filtered

    def test_combined_filters(self, team_with_messages):
        """Test combining multiple filters."""
        message_filter = ChatMessageFilter()
        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Filter for important tag AND v1.0 version
        filter_params = FilterParams(
            column_filters=[
                ColumnFilterData(column="tags", operator="any of", value='["important"]'),
                ColumnFilterData(column="versions", operator="any of", value='["v1.0"]'),
            ]
        )

        filtered = message_filter.apply(queryset, filter_params)

        # Should return only the message with both important tag and v1.0 version
        assert filtered.count() == 1
        assert team_with_messages["important_msg"] in filtered

    def test_no_filters_returns_all(self, team_with_messages):
        """Test that no filters returns all messages."""
        message_filter = ChatMessageFilter()
        queryset = ChatMessage.objects.filter(chat=team_with_messages["chat"])

        # Empty filter params
        filter_params = FilterParams()

        filtered = message_filter.apply(queryset, filter_params)

        assert filtered.count() == 3
        assert all(msg in filtered for msg in team_with_messages["messages"])
