import re
from datetime import datetime

import pandas as pd

from apps.analysis.core import BaseStep, Params, StepError


class WhatsappParser(BaseStep[str, pd.DataFrame]):
    """Parse a whatsapp chat log into a dataframe.

    Assumes the following format:

        01/01/2021, 00:00 - System Message
        01/01/2021, 00:00 - Sender: Message which can
        go across multiple lines
    """

    input_type = str
    output_type = pd.DataFrame

    def run(self, params: Params, data: str) -> tuple[pd.DataFrame, dict]:
        pattern = re.compile(r"^(\d{2}/\d{2}/\d{4},\s\d{2}:\d{2})\s-\s", flags=re.MULTILINE)
        splits = pattern.split(data)
        if len(splits) < 2:
            splits = list(filter(None, splits))
            if not splits:
                self.log.info("No WhatsApp messages found")
                return pd.DataFrame(), {}

            self.log.debug("Unable to parse WhatsApp data:\n" + "\n".join(data.splitlines()[:3]))
            raise StepError("Unable to parse WhatsApp data")
        df = pd.DataFrame(data=[self._get_message(splits[i], splits[i + 1]) for i in range(1, len(splits), 2)])
        df.set_index("date", inplace=True)
        self.log.info(f"Loaded messages from {df.index.min()} to {df.index.max()} ({len(df)} messages)")
        return df, {}

    def _get_message(self, head, tail):
        print(head, tail)
        date = datetime.strptime(head, "%d/%m/%Y, %H:%M")
        if ":" not in tail:
            return {"date": date, "sender": "system", "message": tail.strip()}
        else:
            sender, message = [r.strip() for r in tail.split(":", 1)]
            return {"date": date, "sender": sender, "message": message}
