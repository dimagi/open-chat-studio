from django.urls import reverse


def session_url(*, name, experiment, session):
    """Reverse a session-scoped experiment URL."""
    return reverse(
        name,
        kwargs={
            "team_slug": experiment.team.slug,
            "experiment_id": experiment.public_id,
            "session_id": session.external_id,
        },
    )
