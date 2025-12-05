import json


class PrettyJSONEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        # Force pretty-printing regardless of any caller-provided values.
        kwargs["indent"] = 4
        kwargs["sort_keys"] = True
        super().__init__(*args, **kwargs)
