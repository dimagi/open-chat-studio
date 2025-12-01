from abc import ABC, abstractmethod
from statistics import mean, median, stdev
from typing import Any, ClassVar


class BaseAggregator(ABC):
    """Base class for all aggregators."""

    name: ClassVar[str]
    applies_to: ClassVar[tuple[type, ...]]

    @classmethod
    @abstractmethod
    def compute(cls, values: list) -> Any:
        """Compute the aggregation for a list of values."""
        pass

    @classmethod
    def accepts(cls, value: Any) -> bool:
        """Check if this aggregator can handle the given value type."""
        if isinstance(value, bool):
            return bool in cls.applies_to
        return isinstance(value, cls.applies_to)


class MeanAggregator(BaseAggregator):
    name = "mean"
    applies_to = (int, float)

    @classmethod
    def compute(cls, values: list) -> float:
        return round(mean(values), 4)


class MedianAggregator(BaseAggregator):
    name = "median"
    applies_to = (int, float)

    @classmethod
    def compute(cls, values: list) -> float:
        return round(median(values), 4)


class MinAggregator(BaseAggregator):
    name = "min"
    applies_to = (int, float)

    @classmethod
    def compute(cls, values: list) -> float:
        return min(values)


class MaxAggregator(BaseAggregator):
    name = "max"
    applies_to = (int, float)

    @classmethod
    def compute(cls, values: list) -> float:
        return max(values)


class StdDevAggregator(BaseAggregator):
    name = "std_dev"
    applies_to = (int, float)

    @classmethod
    def compute(cls, values: list) -> float | None:
        if len(values) < 2:
            return None
        return round(stdev(values), 4)


NUMERIC_AGGREGATORS: list[type[BaseAggregator]] = [
    MeanAggregator,
    MedianAggregator,
    MinAggregator,
    MaxAggregator,
    StdDevAggregator,
]

ALL_AGGREGATORS = NUMERIC_AGGREGATORS


def get_aggregators_for_value(value: Any) -> list[type[BaseAggregator]]:
    """Return all aggregators that can handle this value type."""
    return [agg for agg in ALL_AGGREGATORS if agg.accepts(value)]


def aggregate_field(values: list) -> dict:
    """Aggregate a list of values using all applicable aggregators."""
    if not values:
        return {}

    # Determine type from first non-None value
    sample = next((v for v in values if v is not None), None)
    if sample is None:
        return {}

    aggregators = get_aggregators_for_value(sample)
    if not aggregators:
        return {"count": len(values)}

    result = {"type": "numeric", "count": len(values)}
    for agg in aggregators:
        computed = agg.compute(values)
        if computed is not None:
            result[agg.name] = computed

    return result
