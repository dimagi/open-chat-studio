from django.contrib.auth.models import AbstractUser


def serialize_team_creator(creator: AbstractUser | None) -> dict[str, int | str] | None:
    """Return a stable, human-readable identity for a team's creator."""
    if creator is None:
        return None
    return {
        "id": creator.id,
        "username": creator.username,
        "email": creator.email,
    }
