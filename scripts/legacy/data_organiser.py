from datetime import datetime


def organise_headlines(headlines: list) -> list:
    """
    Organise headlines with timestamp.

    :param headlines: List of headline strings
    :return: List of dicts with headline and fetched_at timestamp
    """
    timestamp = datetime.utcnow().isoformat()
    return [{"headline": h, "fetched_at": timestamp} for h in headlines]
