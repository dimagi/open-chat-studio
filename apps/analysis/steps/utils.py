from datetime import datetime

import pandas as pd


def format_truncated_date(date: datetime, truncate_to: str):
    match truncate_to:
        case "seconds" | "secondly":
            return date.isoformat(timespec="seconds")
        case "minutes" | "minutely":
            return date.isoformat(timespec="minutes")
        case "hours" | "hourly":
            return date.isoformat(timespec="hours")
        case "days" | "daily":
            return date.date().isoformat()
        case "weeks" | "weekly":
            return date.date().strftime("%Y-W%W")
        case "months" | "monthly":
            return date.date().strftime("%Y-%m")
        case "quarters" | "quarterly":
            quarter = pd.Timestamp(date).quarter
            year = date.date().strftime("%Y")
            return f"{year}-Q{quarter}"
        case "years" | "yearly":
            return date.date().strftime("%Y")
