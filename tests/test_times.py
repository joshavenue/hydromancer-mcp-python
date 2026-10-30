import pytest

from hydromancer_mcp.times import iso, parse_time

NOW = 1_793_000_000_000  # fixed "now" in Unix ms


@pytest.mark.parametrize(
    ("text", "delta_ms"),
    [
        ("30m", 30 * 60_000),
        ("6h", 6 * 3_600_000),
        ("24h", 24 * 3_600_000),
        ("7d", 7 * 86_400_000),
        ("2w", 14 * 86_400_000),
        ("1.5h", 90 * 60_000),
        ("3 days ago", 3 * 86_400_000),
        ("45 minutes", 45 * 60_000),
        ("12 HRS", 12 * 3_600_000),
    ],
)
def test_relative(text: str, delta_ms: int) -> None:
    assert parse_time(text, now=NOW) == NOW - delta_ms


def test_now_and_empty() -> None:
    assert parse_time("now", now=NOW) == NOW
    assert parse_time(" NOW ", now=NOW) == NOW
    assert parse_time(None) is None
    assert parse_time("") is None


def test_dates_are_utc() -> None:
    assert parse_time("2026-10-01") == 1_790_812_800_000
    assert parse_time("2026-10-01T12:00:00Z") == 1_790_856_000_000
    assert parse_time("2026-10-01T12:00:00") == 1_790_856_000_000  # no offset means UTC
    assert parse_time("2026-10-01T14:00:00+02:00") == 1_790_856_000_000
    assert parse_time("2026-10-01 12:00") == 1_790_856_000_000


def test_epochs() -> None:
    assert parse_time(1_790_812_800) == 1_790_812_800_000  # seconds
    assert parse_time(1_790_812_800_000) == 1_790_812_800_000  # ms
    assert parse_time("1790812800") == 1_790_812_800_000
    assert parse_time("1790812800000") == 1_790_812_800_000
    assert parse_time(1_790_812_800.5) == 1_790_812_800_500


@pytest.mark.parametrize("bad", ["yesterday", "7x", "2026-13-45", "soon", "-5h"])
def test_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError, match="Could not understand the time"):
        parse_time(bad, now=NOW)


def test_rejects_bool() -> None:
    with pytest.raises(ValueError):
        parse_time(True)


def test_iso() -> None:
    assert iso(1_790_812_800_000) == "2026-10-01T00:00:00.000Z"
    assert iso(1_790_812_800_123) == "2026-10-01T00:00:00.123Z"
    assert iso("1790812800000") == "2026-10-01T00:00:00.000Z"
    assert iso(None) is None
    assert iso("nope") is None
