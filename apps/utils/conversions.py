def bytes_to_megabytes(bytes: int) -> float:
    """Converts bytes to megabytes. Base 10 is used, since this is the default for most technologies"""
    return round(bytes / 1000000, 2)
