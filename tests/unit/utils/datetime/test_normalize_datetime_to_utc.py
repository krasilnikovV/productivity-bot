from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from productivity_bot.utils.datetime import normalize_datetime_to_utc


def test_interprets_naive_datetime_in_the_default_timezone() -> None:
    value = datetime.fromisoformat("2026-08-29T14:00:00")

    result = normalize_datetime_to_utc(value, ZoneInfo("Europe/Moscow"))

    assert result == datetime(2026, 8, 29, 11, 0, tzinfo=UTC)


def test_preserves_the_instant_of_timezone_aware_datetime() -> None:
    value = datetime(
        2026,
        8,
        29,
        15,
        0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    result = normalize_datetime_to_utc(value, ZoneInfo("America/New_York"))

    assert result == datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
