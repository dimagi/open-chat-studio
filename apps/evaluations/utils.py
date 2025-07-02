import inspect

from apps.evaluations.models import Evaluator


def get_evaluator_type_info() -> dict[str, dict[str, str]]:
    """
    Get evaluator type information (label, icon) for all available evaluator classes.

    Returns:
        Dict mapping evaluator class names to their schema info (label, icon)
    """
    from apps.evaluations import evaluators

    evaluator_classes = [
        cls
        for _, cls in inspect.getmembers(evaluators, inspect.isclass)
        if issubclass(cls, evaluators.BaseEvaluator) and cls != evaluators.BaseEvaluator
    ]

    evaluator_type_info = {}
    for cls in evaluator_classes:
        evaluator_schema = cls.model_config.get("evaluator_schema")
        if evaluator_schema:
            evaluator_type_info[cls.__name__] = {
                "label": evaluator_schema.label,
                "icon": evaluator_schema.icon,
            }

    return evaluator_type_info


def get_evaluators_with_schema(team) -> list[dict]:
    """
    Get all evaluators for a team with their type information including labels and icons.

    Args:
        team: The team to filter evaluators for

    Returns:
        List of dicts containing evaluator info with schema data
    """
    evaluator_type_info = get_evaluator_type_info()

    evaluators_list = []
    for evaluator in Evaluator.objects.filter(team=team):
        type_info = evaluator_type_info.get(evaluator.type, {})
        evaluators_list.append(
            {
                "id": evaluator.id,
                "name": evaluator.name,
                "type": evaluator.type,
                "label": type_info.get("label", evaluator.type),
                "icon": type_info.get("icon"),
            }
        )

    return evaluators_list


def get_evaluator_type_display(evaluator_type: str) -> dict[str, str]:
    """
    Get display information for a single evaluator type.

    Args:
        evaluator_type: The class name of the evaluator type

    Returns:
        Dict with label and icon for the evaluator type
    """
    evaluator_type_info = get_evaluator_type_info()
    return evaluator_type_info.get(evaluator_type, {"label": evaluator_type, "icon": None})
