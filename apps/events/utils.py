def truncate_dict_items(dictionary, max_limit=100):
    """
    Truncate dictionary items to fit within a char limit. Returns list of (key, value)
    tuples that fit within the limit without breaking up the key value
    """
    formatted_items = []
    chars_used = 0
    for key, val in dictionary.items():
        key_str, val_str = str(key), str(val)
        if chars_used + len(key_str) > max_limit:
            break
        space_for_value = max_limit - chars_used - len(key_str)
        truncated = len(val_str) > space_for_value
        formatted_items.append((key_str, val_str[:space_for_value] + ("..." if truncated else "")))
        if truncated:
            break
        chars_used += len(key_str) + len(val_str)
    return formatted_items
