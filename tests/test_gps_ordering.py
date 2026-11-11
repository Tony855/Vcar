from datetime import datetime, timedelta, timezone

import pytest

from app.modules.locations.gps import is_newer_sample


BASE = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)


def test_first_gps_sample_is_accepted():
    assert is_newer_sample(BASE, None)


def test_strictly_newer_gps_sample_is_accepted():
    assert is_newer_sample(BASE + timedelta(milliseconds=1), BASE)


def test_older_gps_sample_is_rejected():
    assert not is_newer_sample(BASE - timedelta(seconds=1), BASE)


def test_duplicate_timestamp_is_rejected():
    assert not is_newer_sample(BASE, BASE)


def test_equivalent_timestamp_with_different_timezone_is_rejected():
    same_instant = BASE.astimezone(timezone(timedelta(hours=7)))
    assert not is_newer_sample(same_instant, BASE)


@pytest.mark.parametrize("candidate", [
    datetime(2026, 11, 12, 5, 0),
    datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc).replace(tzinfo=None),
])
def test_naive_candidate_timestamp_is_rejected(candidate):
    assert not is_newer_sample(candidate, None)


def test_naive_database_timestamp_fails_closed():
    naive_latest = datetime(2026, 11, 12, 5, 0)
    assert not is_newer_sample(BASE + timedelta(seconds=1), naive_latest)
