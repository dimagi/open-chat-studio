"""Consent as the Chat API reports and enforces it (public channel design, D7).

The store is ``ParticipantData.system_metadata["consent"]``, shared with CommCare Connect and read
by ``ConsentCheckStage``. The text is the frozen ``ConsentForm`` on the version the session runs
against, so a republished form re-prompts through a new ``form_version_id``.
"""

from rest_framework import status
from rest_framework.response import Response

from apps.channels.widget_versions import WIDGET_VERSION_HEADER, widget_enforces_consent
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData


def participant_data_for(session: ExperimentSession) -> ParticipantData | None:
    return ParticipantData.objects.filter(participant=session.participant, experiment=session.experiment).first()


def consent_block(version: Experiment, participant_data: ParticipantData | None) -> dict:
    form = version.consent_form
    if form is None:
        return {"required": False, "form_version_id": None, "text": None}
    consented = participant_data is not None and participant_data.has_consented()
    return {
        "required": not consented,
        "form_version_id": form.id,
        "text": None if consented else form.get_rendered_content(),
    }


def consent_refusal(request, session: ExperimentSession, version: Experiment) -> Response | None:
    """The 403 that holds a message until consent is recorded, or None.

    Only widgets from ``CONSENT_INTRODUCED`` on are refused: older widgets treat every 403 as a
    dead session, and non-widget API callers have no consent surface.
    """
    if not widget_enforces_consent(request.headers.get(WIDGET_VERSION_HEADER)):
        return None
    block = consent_block(version, participant_data_for(session))
    if not block["required"]:
        return None
    return Response(
        {"error": "Consent is required before chatting", "code": "consent_required", "consent": block},
        status=status.HTTP_403_FORBIDDEN,
    )
