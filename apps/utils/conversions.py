def bytes_to_megabytes(bytes: int) -> float:
    """Converts bytes to megabytes (base 2)"""
    return round(bytes / 1048576, 2)
