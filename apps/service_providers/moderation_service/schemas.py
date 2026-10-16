from pydantic import BaseModel, Field, RootModel


class ModerationCategory(BaseModel):
    flagged: bool
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class ModerationOutput(RootModel[dict[str, ModerationCategory]]):
    """Category name to the provider's result for that category."""

    def flagged_categories(self) -> set[str]:
        return {name for name, category in self.root.items() if category.flagged}
