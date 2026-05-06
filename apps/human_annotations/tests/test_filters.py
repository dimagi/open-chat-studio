import pytest
from django.http import QueryDict
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.human_annotations.filters import AnnotationSessionFilter
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _get_querydict(params: dict) -> QueryDict:
    qd = QueryDict("", mutable=True)
    qd.update(params)
    return qd


@pytest.mark.django_db()
def test_annotation_session_filter_message_date_no_duplicates():
    """The annotation queue session filter must not return duplicate rows when filtering
    by message date — the underlying ``chat__messages__created_at`` traversal is one-to-many
    so it requires EXISTS, not a JOIN. Regression guard for the previously-missed wiring."""
    session = ExperimentSessionFactory.create()
    for content in ("first", "second", "third"):
        ChatMessage.objects.create(chat=session.chat, content=content, message_type=ChatMessageType.HUMAN)

    today = timezone.now().date().isoformat()
    params = {
        "filter_0_column": "message_date",
        "filter_0_operator": Operators.ON,
        "filter_0_value": today,
    }
    filtered = AnnotationSessionFilter().apply(session.experiment.sessions.all(), FilterParams(_get_querydict(params)))

    assert filtered.count() == 1
    assert list(filtered) == [session]
