import re
from datetime import datetime

import pandas as pd

from apps.analysis.core import BaseStep, Params, StepContext
from apps.analysis.exceptions import StepError


class WhatsappParserParams(Params):
    """Parameters for the WhatsappParser step."""

    remove_deleted_messages: bool = True
    remove_system_messages: bool = True
    remove_media_omitted_messages: bool = True

    def get_dynamic_config_form_class(self):
        from apps.analysis.steps.forms import WhatsappParserParamsForm

        return WhatsappParserParamsForm


class WhatsappParser(BaseStep[str, pd.DataFrame]):
    """Parse a whatsapp chat log into a dataframe.

    Assumes the following format:

        01/01/2021, 00:00 - System Message
        01/01/2021, 00:00 - Sender: Message which can
        go across multiple lines
    """

    input_type = str
    output_type = pd.DataFrame
    param_schema = WhatsappParserParams

    def run(self, params: Params, context: StepContext[str]) -> StepContext[pd.DataFrame]:
        pattern = re.compile(r"^(\d{2}/\d{2}/\d{4},\s\d{2}:\d{2})\s-\s", flags=re.MULTILINE)
        data = context.get_data()
        splits = pattern.split(data)
        if len(splits) < 2:
            splits = list(filter(None, splits))
            if not splits:
                self.log.info("No WhatsApp messages found")
                return StepContext(pd.DataFrame(), name="whatsapp_data")

            self.log.debug("Unable to parse WhatsApp data:\n" + "\n".join(data.splitlines()[:3]))
            raise StepError("Unable to parse WhatsApp data")
        messages = [self._get_message(splits[i], splits[i + 1]) for i in range(1, len(splits), 2)]
        messages = self._filter_messages(messages)
        df = pd.DataFrame(data=messages)
        df.set_index("date", inplace=True)
        self.log.info(f"Loaded messages from {df.index.min()} to {df.index.max()} ({len(df)} messages)")
        return StepContext(df, name="whatsapp_data")

    def _get_message(self, head, tail):
        date = datetime.strptime(head, "%d/%m/%Y, %H:%M")
        if ":" not in tail:
            return {"date": date, "sender": "system", "message": tail.strip()}
        else:
            sender, message = (r.strip() for r in tail.split(":", 1))
            return {"date": date, "sender": sender, "message": message}

    def _filter_messages(self, messages):
        def _remove_message(message):
            match message:
                case {"sender": "system"} if self._params.remove_system_messages:
                    return True
                case {"message": "This message was deleted"} if self._params.remove_deleted_messages:
                    return True
                case {"message": "<Media omitted>"} if self._params.remove_media_omitted_messages:
                    return True
                case _:
                    return False

        return [m for m in messages if not _remove_message(m)]
