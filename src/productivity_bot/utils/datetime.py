from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def normalize_datetime_to_utc(
    value: datetime,
    default_timezone: ZoneInfo,
) -> datetime:
    """Return a UTC datetime, using the default timezone when none is present."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=default_timezone)
    return value.astimezone(UTC)
