from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SyntheticVoiceFactory


def add_remaining_resources(team):
    """Add the resources `make_team_with_resources` leaves out, so every option list has an entry."""
    CollectionFactory.create(
        team=team,
        name="Support KB",
        is_index=True,
        is_remote_index=True,
        llm_provider=None,
        embedding_provider_model=None,
    )
    CustomActionFactory.create(team=team, name="Orders API")
    # One voice tied to the team's provider and one of the shared AWS voices, which carry no provider.
    SyntheticVoiceFactory.create(name="Joanna", service="AWS", voice_provider=team.voiceprovider_set.first())
    SyntheticVoiceFactory.create(name="Matthew", service="AWS")
    return team
