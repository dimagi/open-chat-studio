import uuid

from celery.app import shared_task
from celery.utils.log import get_task_logger
from django.db import OperationalError  # noqa: F811 - used at runtime in task decorator
from django.utils import timezone
from field_audit.models import AuditAction
from taskbadger.celery import Task as TaskbadgerTask
from telebot import types
from twilio.request_validator import RequestValidator

from apps.channels import widget_versions
from apps.channels.api_channel import ApiChannel
from apps.channels.clients.connect_client import CommCareConnectClient, Message
from apps.channels.connect_channel import CommCareConnectChannel
from apps.channels.datamodels import (
    BaseMessage,
    SureAdhereMessage,
    TelegramMessage,
    TwilioMessage,
    WhatsAppMessage,
)
from apps.channels.datamodels import EmailMessage as EmailMessageDatamodel
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.facebook_channel import FacebookMessengerChannel
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.sureadhere_channel import SureAdhereChannel
from apps.channels.telegram_channel import TelegramChannel
from apps.channels.whatsapp_channel import WhatsappChannel
from apps.chat.models import ChatMessage
from apps.chatbots.version_resolver import resolve_published_or_working
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.ocs_notifications.notifications import widget_auth_level_upgrade_notification
from apps.service_providers.models import MessagingProviderType
from apps.teams.utils import set_current_team
from apps.utils.taskbadger import update_taskbadger_data

log = get_task_logger("ocs.channels")


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_telegram_message(self, message_data: str, channel_external_id: uuid):
    experiment_channel = get_experiment_channel(ChannelPlatform.TELEGRAM, external_id=channel_external_id)
    if not experiment_channel:
        log.info(f"No experiment channel found for external_id: {channel_external_id}")
        return

    update = types.Update.de_json(message_data)
    if update.my_chat_member:
        # This is a chat member update that we don't care about.
        # See https://core.telegram.org/bots/api-changelog#march-9-2021
        return

    message = TelegramMessage.parse(update)
    message_handler = TelegramChannel(resolve_published_or_working(experiment_channel.experiment), experiment_channel)
    update_taskbadger_data(self, message_handler, message)

    message_handler.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_twilio_message(self, message_data: dict):
    message = TwilioMessage.parse(message_data)

    ChannelClass, channel_id_key = get_twilio_channel_class_and_key(message)

    experiment_channel = get_experiment_channel(
        message.platform,
        extra_data__contains={channel_id_key: message.to},
        messaging_provider__type=MessagingProviderType.twilio,
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for {channel_id_key}: {message.to}")
        return

    message_handler = ChannelClass(
        resolve_published_or_working(experiment_channel.experiment), experiment_channel=experiment_channel
    )
    update_taskbadger_data(self, message_handler, message)

    message_handler.new_user_message(message)


def get_twilio_channel_class_and_key(message):
    match message.platform:
        case ChannelPlatform.WHATSAPP:
            return WhatsappChannel, "number"
        case ChannelPlatform.FACEBOOK:
            return FacebookMessengerChannel, "page_id"
    raise ValueError(f"Unsupported Twilio platform: {message.platform}")


def validate_twillio_request(experiment_channel, raw_data, request_uri, signature) -> bool:
    """For now this just logs an error if the signature validation fails.
    In the future we will want to raise an error.

    See https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    try:
        auth_token = experiment_channel.messaging_provider.get_messaging_service().auth_token
        return RequestValidator(auth_token).validate(request_uri, raw_data, signature)
    except Exception:
        log.exception("Twilio signature validation failed")
        return False


@shared_task(bind=True, base=TaskbadgerTask)
def handle_sureadhere_message(self, sureadhere_tenant_id: str, message_data: dict):
    message = SureAdhereMessage.parse(message_data)
    experiment_channel = get_experiment_channel(
        ChannelPlatform.SUREADHERE,
        extra_data__sureadhere_tenant_id=sureadhere_tenant_id,
        messaging_provider__type=MessagingProviderType.sureadhere,
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for SureAdhere tenant ID: {sureadhere_tenant_id}")
        return
    channel = SureAdhereChannel(resolve_published_or_working(experiment_channel.experiment), experiment_channel)
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_turn_message(self, experiment_id: uuid, message_data: dict):
    message = WhatsAppMessage.parse(message_data)
    experiment_channel = get_experiment_channel(
        ChannelPlatform.WHATSAPP,
        experiment__public_id=experiment_id,
        messaging_provider__type=MessagingProviderType.turnio,
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for experiment_id: {experiment_id}")
        return
    set_current_team(experiment_channel.team)
    channel = WhatsappChannel(resolve_published_or_working(experiment_channel.experiment), experiment_channel)
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


def handle_api_message(
    user, experiment_version, experiment_channel, message_text: str, participant_id: str, session=None
) -> ChatMessage:
    """Synchronously handles the message coming from the API"""
    message = BaseMessage(participant_id=participant_id, message_text=message_text)
    channel = ApiChannel(
        experiment_version,
        experiment_channel,
        experiment_session=session,
        user=user,
    )
    return channel.new_user_message(message)


def handle_evaluation_message(
    experiment_version, experiment_channel, message_text: str, session: ExperimentSession, participant_data: dict
) -> ChatMessage:
    """Synchronously handles the message coming from evaluations"""
    message = BaseMessage(participant_id=session.participant.identifier, message_text=message_text)
    channel = EvaluationChannel(
        experiment_version, experiment_channel, experiment_session=session, participant_data=participant_data
    )
    return channel.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_commcare_connect_message(self, experiment_id: int, participant_data_id: int, messages: list[Message]):
    participant_data = ParticipantData.objects.prefetch_related("participant").get(id=participant_data_id)
    experiment_channel = get_experiment_channel(ChannelPlatform.COMMCARE_CONNECT, experiment_id=experiment_id)
    if not experiment_channel:
        log.info(f"No experiment channel found for experiment_id: {experiment_id}")
        return

    messages.sort(key=lambda x: x["timestamp"])

    connect_client = CommCareConnectClient()
    decrypted_messages = connect_client.decrypt_messages(participant_data.get_encryption_key_bytes(), messages=messages)

    # If the user sent multiple messages, we should append it together instead of the bot replying to each one
    user_message = "\n\n".join(decrypted_messages)

    message = BaseMessage(participant_id=participant_data.participant.identifier, message_text=user_message)
    channel = CommCareConnectChannel(
        experiment=resolve_published_or_working(experiment_channel.experiment), experiment_channel=experiment_channel
    )

    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


def get_experiment_channel(platform, **query_kwargs):
    query = get_experiment_channel_base_query(platform, **query_kwargs)
    return query.select_related("experiment", "team", "messaging_provider").first()


def get_experiment_channel_base_query(platform, **query_kwargs):
    return ExperimentChannel.objects.filter(
        platform=platform,
        **query_kwargs,
    ).filter(experiment__is_archived=False)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_meta_cloud_api_message(self, channel_id: int, team_slug: str, message_data: dict):
    message = WhatsAppMessage.parse(message_data)
    experiment_channel = (
        ExperimentChannel.objects.filter(
            id=channel_id,
            experiment__is_archived=False,
        )
        .select_related("experiment", "team", "messaging_provider")
        .first()
    )
    if not experiment_channel:
        log.info("No experiment channel found for channel_id=%s team=%s", channel_id, team_slug)
        return

    set_current_team(experiment_channel.team)
    channel = WhatsappChannel(resolve_published_or_working(experiment_channel.experiment), experiment_channel)
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


@shared_task(
    bind=True,
    base=TaskbadgerTask,
    ignore_result=True,
    autoretry_for=(OperationalError, ConnectionError),
    max_retries=3,
    retry_backoff=60,
    retry_backoff_max=300,
    retry_jitter=True,
)
def handle_email_message(self, email_data: dict, channel_id: int | None = None, session_id: int | None = None):
    from apps.channels.email_channel import (  # noqa: PLC0415 - tests patch EmailChannel at source module
        EmailChannel,
        EmailThreadContext,
        get_email_experiment_channel,
    )

    message = EmailMessageDatamodel(**email_data)

    if channel_id is not None:
        # Post-deploy payload: routing already happened in the webhook handler.
        try:
            experiment_channel = ExperimentChannel.objects.select_related("experiment", "team").get(id=channel_id)
        except ExperimentChannel.DoesNotExist:
            log.info("Email channel id=%s no longer exists, ignoring", channel_id)
            return
        session = None
        if session_id is not None:
            try:
                session = ExperimentSession.objects.select_related("team", "participant", "experiment_channel").get(
                    id=session_id
                )
            except ExperimentSession.DoesNotExist:
                # Session was deleted between enqueue and dequeue; let the
                # pipeline create a fresh one rather than dropping the message.
                session = None
    else:
        # Legacy payload: in-flight tasks queued before this deploy don't
        # carry channel_id. Resolve by routing as the previous version did.
        experiment_channel, session = get_email_experiment_channel(
            in_reply_to=message.in_reply_to,
            references=message.references,
            to_address=message.to_address,
            sender_address=message.from_address,
        )
        if not experiment_channel:
            log.info("No email channel found for to=%s, ignoring", message.to_address)
            return

    set_current_team(experiment_channel.team)

    thread_context = EmailThreadContext.from_inbound(message)

    channel = EmailChannel(
        experiment=resolve_published_or_working(experiment_channel.experiment),
        experiment_channel=experiment_channel,
        experiment_session=session,
        thread_context=thread_context,
    )
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


@shared_task(ignore_result=True)
def ratchet_widget_auth_levels():
    """Raise required_auth_level for embedded-widget channels whose widget has upgraded.

    Two-phase per channel: on first detection the team is notified (with the minimum
    widget version the new level needs) and the pending level is recorded; once the
    grace period elapses the level is applied. Monotonic — never lowers a level, so a
    spoofed or stale version header can only tighten auth, never relax it.
    """
    now = timezone.now()
    channels = (
        ExperimentChannel.objects.filter(platform=ChannelPlatform.EMBEDDED_WIDGET, deleted=False)
        .select_related("experiment", "team")
        .order_by("team_id")
    )

    notify_by_team: dict[int, dict] = {}
    for channel in channels:
        target = widget_versions.level_for_version(channel.widget_version)

        if channel.auth_level_notified_at is None:
            # No bump pending yet. Start one only if the widget now implies a higher level.
            if target <= channel.required_auth_level:
                continue
            # Defer recording the pending state until the team notification succeeds (below),
            # so a swallowed notification failure never silently starts the grace clock.
            team_data = notify_by_team.setdefault(
                channel.team_id, {"team": channel.team, "chatbots": {}, "min_level": target, "pending": []}
            )
            team_data["chatbots"][channel.experiment.name] = channel.experiment.get_absolute_url()
            team_data["min_level"] = max(team_data["min_level"], target)
            team_data["pending"].append((channel.pk, target))
        elif target < channel.pending_auth_level:
            # The widget dropped back below the *pending* level before the grace period
            # elapsed; abandon the raise (ADR-0045). Comparing against the pending level
            # (not the current floor) also covers grandfathered NONE channels, where an
            # intermediate downgrade stays above the floor but below the pending level.
            _clear_pending_auth_level(channel)
        elif now - channel.auth_level_notified_at >= ExperimentChannel.AUTH_LEVEL_RATCHET_GRACE:
            channel.required_auth_level = channel.pending_auth_level
            channel.pending_auth_level = None
            channel.auth_level_notified_at = None
            # save() routes through the audited manager so the level change is recorded;
            # update_fields keeps concurrent widget_version telemetry writes from being clobbered.
            channel.save(update_fields=["required_auth_level", "pending_auth_level", "auth_level_notified_at"])

    effective_date = now + ExperimentChannel.AUTH_LEVEL_RATCHET_GRACE
    for data in notify_by_team.values():
        notified = widget_auth_level_upgrade_notification(
            team=data["team"],
            affected_chatbots=data["chatbots"],
            min_version=widget_versions.min_version_for_level(data["min_level"]),
            effective_date=effective_date,
            docs_url=widget_versions.widget_docs_url(),
        )
        if not notified:
            # Notification creation failed (and was swallowed); leave these channels
            # untouched so the next run retries rather than ratcheting them unannounced.
            continue
        for pk, target in data["pending"]:
            # Pending state is workflow bookkeeping, not an audited field change (like the
            # widget_version telemetry writes); bypass auditing so it creates no empty events.
            ExperimentChannel.objects.filter(pk=pk).update(
                pending_auth_level=target, auth_level_notified_at=now, audit_action=AuditAction.IGNORE
            )


def _clear_pending_auth_level(channel: ExperimentChannel) -> None:
    ExperimentChannel.objects.filter(pk=channel.pk).update(
        pending_auth_level=None, auth_level_notified_at=None, audit_action=AuditAction.IGNORE
    )
