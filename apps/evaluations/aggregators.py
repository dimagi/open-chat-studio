from abc import ABC, abstractmethod
from collections import Counter
from statistics import mean, median, stdev
from typing import Any, ClassVar


class BaseAggregator(ABC):
    """Base class for all aggregators."""

    name: ClassVar[str]
    field_type: ClassVar[str]
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


class BaseNumericAggregator(BaseAggregator):
    """Base class for numeric aggregators."""

    field_type = "numeric"
    applies_to = (int, float)


class BaseCategoricalAggregator(BaseAggregator):
    """Base class for categorical aggregators."""

    field_type = "categorical"
    applies_to = (str, bool)


class MeanAggregator(BaseNumericAggregator):
    name = "mean"

    @classmethod
    def compute(cls, values: list) -> float:
        return round(mean(values), 4)


class MedianAggregator(BaseNumericAggregator):
    name = "median"

    @classmethod
    def compute(cls, values: list) -> float:
        return round(median(values), 4)


class MinAggregator(BaseNumericAggregator):
    name = "min"

    @classmethod
    def compute(cls, values: list) -> float:
        return min(values)


class MaxAggregator(BaseNumericAggregator):
    name = "max"

    @classmethod
    def compute(cls, values: list) -> float:
        return max(values)


class StdDevAggregator(BaseNumericAggregator):
    name = "std_dev"

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


class DistributionAggregator(BaseCategoricalAggregator):
    name = "distribution"

    @classmethod
    def compute(cls, values: list) -> dict[str, float]:
        counter = Counter(values)
        total = len(values)
        return {str(k): round(v / total * 100, 1) for k, v in counter.most_common()}


class ModeAggregator(BaseCategoricalAggregator):
    name = "mode"

    @classmethod
    def compute(cls, values: list) -> str | None:
        if not values:
            return None
        return str(Counter(values).most_common(1)[0][0])


CATEGORICAL_AGGREGATORS: list[type[BaseAggregator]] = [
    DistributionAggregator,
    ModeAggregator,
]

ALL_AGGREGATORS = NUMERIC_AGGREGATORS + CATEGORICAL_AGGREGATORS


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

    result = {"type": aggregators[0].field_type, "count": len(values)}
    for agg in aggregators:
        computed = agg.compute(values)
        if computed is not None:
            result[agg.name] = computed

    return result
