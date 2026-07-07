from __future__ import annotations

from typing import TYPE_CHECKING

from apps.channels.api_channel import ApiChannel
from apps.channels.channels_v2.email_channel import EmailChannel
from apps.channels.channels_v2.facebook_channel import FacebookMessengerChannel
from apps.channels.channels_v2.slack_channel import SlackChannel
from apps.channels.channels_v2.sureadhere_channel import SureAdhereChannel
from apps.channels.channels_v2.telegram_channel import TelegramChannel
from apps.channels.channels_v2.web_channel import WebChannel
from apps.channels.channels_v2.whatsapp_channel import WhatsappChannel
from apps.channels.connect_channel import CommCareConnectChannel
from apps.channels.models import ChannelPlatform

if TYPE_CHECKING:
    from apps.channels.channel_base import ChannelBase
    from apps.experiments.models import ExperimentSession

# The evaluations platform is deliberately absent; EvaluationChannel is constructed directly with a bot.
PLATFORM_CHANNEL_CLASSES = {
    ChannelPlatform.TELEGRAM: TelegramChannel,
    ChannelPlatform.WEB: WebChannel,
    ChannelPlatform.WHATSAPP: WhatsappChannel,
    ChannelPlatform.FACEBOOK: FacebookMessengerChannel,
    ChannelPlatform.SUREADHERE: SureAdhereChannel,
    ChannelPlatform.API: ApiChannel,
    ChannelPlatform.SLACK: SlackChannel,
    ChannelPlatform.COMMCARE_CONNECT: CommCareConnectChannel,
    ChannelPlatform.EMBEDDED_WIDGET: ApiChannel,
    ChannelPlatform.EMAIL: EmailChannel,
}


def get_channel_class_for_platform(platform: ChannelPlatform | str) -> type[ChannelBase]:
    try:
        return PLATFORM_CHANNEL_CLASSES[platform]
    except KeyError:
        raise Exception(f"Unsupported platform type {platform}") from None


def from_experiment_session(experiment_session: ExperimentSession) -> ChannelBase:
    """Returns the correct ChannelBase subclass instance for the session's channel platform."""
    channel_cls = get_channel_class_for_platform(experiment_session.experiment_channel.platform)
    return channel_cls(
        experiment_session.experiment,
        experiment_channel=experiment_session.experiment_channel,
        experiment_session=experiment_session,
    )
