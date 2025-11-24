from collections.abc import Sequence
from typing import ClassVar

from apps.experiments.filters import ChannelsFilter
from apps.web.dynamic_filters.base import ColumnFilter, MultiColumnFilter, StringColumnFilter
from apps.web.dynamic_filters.column_filters import TimestampFilter


class ParticipantFilter(MultiColumnFilter):
    """Filter for experiment sessions using the new ColumnFilter pattern."""

    filters: ClassVar[Sequence[ColumnFilter]] = [
        TimestampFilter(label="Created On", column="created_at", query_param="created_on"),
        StringColumnFilter(
            label="Name/Identifier",
            columns=["identifier", "name"],  # OR search across both fields
            query_param="participant",
        ),
        StringColumnFilter(label="Remote ID", columns=["remote_id"], query_param="remote_id"),
        ChannelsFilter(column="platform"),
    ]
