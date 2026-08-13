from collections.abc import Iterable


def get_real_user_or_none(user):
    if user.is_anonymous:
        return None
    else:
        return user


def normalize_participant_allowlist(identifiers: Iterable[str]) -> list[str]:
    """Strip the spaces out of each participant identifier and drop the blanks.

    ``Experiment.is_participant_allowed`` compares identifiers exactly, so a human-formatted phone
    number stored verbatim ("+27 82 000 0000") never matches the identifier a channel actually
    reports ("+27820000000") -- an allowlist that looks configured and admits nobody.
    """
    return [stripped for identifier in identifiers if (stripped := identifier.replace(" ", ""))]
