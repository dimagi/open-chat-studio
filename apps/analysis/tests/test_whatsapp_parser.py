from textwrap import dedent

import pandas as pd
import pytest

from apps.analysis.core import Params, PipelineContext, StepError
from apps.analysis.steps.parsers import WhatsappParser


@pytest.fixture
def whatsapp_parser():
    step = WhatsappParser()
    step.initialize(PipelineContext(None))
    return step


@pytest.fixture
def valid_whatsapp_log():
    return dedent(
        """
    01/01/2021, 00:00 - System Message
    01/01/2021, 00:01 - User1: Hello World
    06/01/2021, 00:02 - User2: <Media Deleted>
    21/01/2021, 00:03 - User1: Let's meet at 10:00
    We can meet at the cafe
    """
    ).strip()


def test_whatsapp_parser_parses_valid_log(whatsapp_parser, valid_whatsapp_log):
    params = Params()
    df, _ = whatsapp_parser.run(params, valid_whatsapp_log)
    assert not df.empty
    _check_message(df, "2021-01-01 00:00", "system", "System Message")
    _check_message(df, "2021-01-01 00:01", "User1", "Hello World")
    _check_message(df, "2021-01-06 00:02", "User2", "<Media Deleted>")
    _check_message(df, "2021-01-21 00:03", "User1", "Let's meet at 10:00\nWe can meet at the cafe")


def _check_message(df, date, sender, message):
    assert df.loc[pd.Timestamp(date)]["sender"] == sender
    assert df.loc[pd.Timestamp(date)]["message"] == message


def test_whatsapp_parser_handles_invalid_log(whatsapp_parser):
    params = Params()
    with pytest.raises(StepError):
        whatsapp_parser.run(params, "This is not a valid whatsapp log on 01/01/2021, 00:00")


def test_whatsapp_parser_handles_empty_log(whatsapp_parser):
    params = Params()
    df, _ = whatsapp_parser.run(params, "")
    assert df.empty
