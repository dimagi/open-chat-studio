import re
from datetime import datetime

import pandas as pd

from apps.analysis.core import BaseStep, Params


class WhatsappParser(BaseStep[str, pd.DataFrame]):
    input_type = str
    output_type = pd.DataFrame

    def run(self, params: Params, data: str) -> tuple[pd.DataFrame, dict]:
        pattern = re.compile(r"^(\d{2}/\d{2}/\d{4},\s\d{2}:\d{2})\s-\s", flags=re.MULTILINE)
        splits = pattern.split(data)
        df = pd.DataFrame(data=[self._get_message(splits[i], splits[i + 1]) for i in range(1, len(splits), 2)])
        df.set_index("date", inplace=True)
        self.log.info(f"Loaded messages from {df.index.min()} to {df.index.max()} ({len(df)} messages)")
        return df, {}

    def _get_message(self, head, tail):
        date = datetime.strptime(head, "%d/%m/%Y, %H:%M")
        if ":" not in tail:
            return {"date": date, "sender": "system", "message": tail}
        else:
            sender, message = [r.strip() for r in tail.split(":", 1)]
            return {"date": date, "sender": sender, "message": message}
