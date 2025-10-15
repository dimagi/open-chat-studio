MEGA = 1000000
KILO = 1000


def humanize_bytes(num: int):
    if num < KILO:
        return f"{num} B"
    if num < MEGA:
        return f"{bytes_to_kilobytes(num)} KB"
    return f"{bytes_to_megabytes(num)} MB"


def bytes_to_kilobytes(num: int) -> float:
    """Converts bytes to kilobytes. Base 10 is used, since this is the default for most technologies"""
    return round(num / KILO, 2)


def bytes_to_megabytes(num: int) -> float:
    """Converts bytes to megabytes. Base 10 is used, since this is the default for most technologies"""
    return round(num / MEGA, 2)
