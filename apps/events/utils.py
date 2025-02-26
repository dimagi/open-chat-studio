def truncate_dict_items(dictionary, max_limit=100):
    """
    Truncate dictionary values to fit within a char limit for each value.
    """
    formatted_items = []
    for key, val in dictionary.items():
        key_str, val_str = str(key), str(val)
        if len(val_str) > max_limit:
            truncated_val = val_str[:max_limit] + "..."
        else:
            truncated_val = val_str
        formatted_items.append((key_str, truncated_val))
    return formatted_items
