from pydantic import BaseModel, RootModel


class ModerationCategory(BaseModel):
    flagged: bool
    score: float


class ModerationOutput(RootModel[dict[str, ModerationCategory]]):
    """Category name to the provider's result for that category."""

    def flagged_categories(self) -> set[str]:
        return {name for name, category in self.root.items() if category.flagged}
