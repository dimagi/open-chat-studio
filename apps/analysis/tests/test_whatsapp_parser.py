from pathlib import Path
from textwrap import dedent

import pandas as pd
import pytest

from apps.analysis.core import Params, PipelineContext, StepContext
from apps.analysis.exceptions import StepError
from apps.analysis.steps.parsers import WhatsappParser, WhatsappParserParams


@pytest.fixture()
def whatsapp_parser():
    step = WhatsappParser()
    step.initialize(PipelineContext())
    return step


@pytest.fixture()
def valid_whatsapp_log():
    return dedent(
        """
    01/01/2021, 00:00 - System Message
    01/01/2021, 00:01 - User1: Hello World
    06/01/2021, 00:02 - User2: <Media omitted>
    21/01/2021, 00:03 - User1: Let's meet at 10:00
    We can meet at the cafe
    21/01/2021, 00:04 - User3: This message was deleted
    """
    ).strip()


@pytest.fixture()
def valid_whatsapp_log_unicode_rtl():
    return Path(__file__).parent.joinpath("data/unicode_rtl_whatsapp_data.txt").read_text()


def test_whatsapp_parser_parses_valid_log(whatsapp_parser, valid_whatsapp_log):
    params = WhatsappParserParams(
        remove_deleted_messages=False, remove_system_messages=False, remove_media_omitted_messages=False
    )
    whatsapp_parser.initialize(PipelineContext(params=params.model_dump()))
    result = whatsapp_parser.run(params, StepContext(valid_whatsapp_log))
    df = result.data
    assert len(df) == 5
    _check_message(df, "2021-01-01 00:00", "system", "System Message")
    _check_message(df, "2021-01-01 00:01", "User1", "Hello World")
    _check_message(df, "2021-01-06 00:02", "User2", "<Media omitted>")
    _check_message(df, "2021-01-21 00:03", "User1", "Let's meet at 10:00\nWe can meet at the cafe")
    _check_message(df, "2021-01-21 00:04", "User3", "This message was deleted")


def test_whatsapp_parser_message_filtering(whatsapp_parser, valid_whatsapp_log):
    result = whatsapp_parser.run(whatsapp_parser._params, StepContext(valid_whatsapp_log))
    df = result.data
    assert len(df) == 2
    _check_message(df, "2021-01-01 00:01", "User1", "Hello World")
    _check_message(df, "2021-01-21 00:03", "User1", "Let's meet at 10:00\nWe can meet at the cafe")


def test_whatsapp_parser_parses_valid_log_unicode_rtl(whatsapp_parser, valid_whatsapp_log_unicode_rtl):
    params = Params()
    result = whatsapp_parser.run(params, StepContext(valid_whatsapp_log_unicode_rtl))
    df = result.data
    assert not df.empty
    _check_message(df, "2023-03-11 21:27", "User1", "Hello")
    _check_message(df, "2023-03-11 21:28", "User2", "Hi\nHow are you?")
    _check_message(
        df,
        "2023-07-13 15:54",
        "123456",
        "اولا.لسلام عليكم  : كل من كان هذه المجموعة.  بعد السلام: اشكركم"
        "\n\n  جميعا خاصة المعلمون المدرسون فى المجموعة. اتمنى  لكم النجاح.\n‏",
    )
    _check_message(df, "2023-07-13 16:23", "123123", "وعليكم السلام مرحبا يااخي💙🌸")
    _check_message(df, "2023-07-13 20:28", "Coach", "Halkan baad ka daawan kartaan casharka oo muuqaal ah.")


def _check_message(df, date, sender, message):
    assert df.loc[pd.Timestamp(date)]["sender"] == sender
    assert df.loc[pd.Timestamp(date)]["message"] == message


def test_whatsapp_parser_handles_invalid_log(whatsapp_parser):
    params = Params()
    with pytest.raises(StepError):
        whatsapp_parser.run(params, StepContext("This is not a valid whatsapp log on 01/01/2021, 00:00"))


def test_whatsapp_parser_handles_empty_log(whatsapp_parser):
    params = Params()
    result = whatsapp_parser.run(params, StepContext(""))
    assert result.data.empty
