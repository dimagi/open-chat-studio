def merge_raw_params(*params: dict) -> dict:
    """Merge raw params from multiple steps into a single dict."""
    merged = {}
    for step_params in params:
        for step_name, step_param in step_params.items():
            if isinstance(step_param, dict):
                merged[step_name] = {**merged.get(step_name, {}), **step_param}
            else:
                merged[step_name] = step_param
    return merged
