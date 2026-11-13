from scripts.load_test_locations import build_report, meets_acceptance_thresholds, percentile


def test_percentile_handles_empty_and_small_samples():
    assert percentile([], 0.95) == 0.0
    assert percentile([10.0, 20.0, 30.0], 0.50) == 20.0
    assert percentile([10.0, 20.0, 30.0], 0.95) == 30.0


def test_build_report_exposes_delivery_and_latency_percentiles():
    report = build_report(
        requested_connections=100, connected=95, messages_sent=3,
        frames_received=270, connect_latencies_ms=[10, 20, 30, 40],
        fanout_latencies_ms=[5, 15, 25, 35], failures=10, elapsed_seconds=2.0,
    )
    assert report["connection_success_rate"] == 0.95
    assert report["frame_delivery_rate"] == 0.9474
    assert report["expected_frames"] == 285
    assert report["connect_latency_ms"]["p95"] == 40
    assert report["fanout_latency_ms"]["p50"] == 25
    assert report["gps_publish_per_second"] == 1.5


def test_build_report_zero_denominators_are_safe():
    report = build_report(
        requested_connections=0, connected=0, messages_sent=0,
        frames_received=0, connect_latencies_ms=[], fanout_latencies_ms=[],
        failures=0, elapsed_seconds=0,
    )
    assert report["connection_success_rate"] == 0
    assert report["frame_delivery_rate"] == 0
    assert report["gps_publish_per_second"] == 0

from scripts.load_test_locations import fleet_websocket_url, is_fleet_location_event


def test_fleet_websocket_url_uses_secure_scheme_and_encodes_ticket():
    assert fleet_websocket_url("https://staging.example", "a+b/c") == (
        "wss://staging.example/api/v1/admin/fleet/stream?ticket=a%2Bb%2Fc"
    )
    assert fleet_websocket_url("http://localhost:8080", "ticket") == (
        "ws://localhost:8080/api/v1/admin/fleet/stream?ticket=ticket"
    )


def test_fleet_websocket_url_rejects_unsupported_scheme():
    import pytest
    with pytest.raises(ValueError):
        fleet_websocket_url("ftp://staging.example", "ticket")


def test_fleet_event_validation_rejects_non_location_frames():
    import json

    event = {
        "telegram_id": 123,
        "latitude": 11.5,
        "longitude": 104.9,
        "recorded_at": "2026-11-12T00:00:00+00:00",
    }
    assert is_fleet_location_event(json.dumps(event))
    assert not is_fleet_location_event('{"type":"ready"}')
    assert not is_fleet_location_event("not-json")


def test_build_report_includes_initial_and_reconnect_snapshot_rates():
    report = build_report(
        requested_connections=20,
        connected=20,
        messages_sent=2,
        frames_received=40,
        connect_latencies_ms=[10],
        fanout_latencies_ms=[5],
        failures=0,
        elapsed_seconds=2,
        initial_snapshot_attempts=20,
        initial_snapshots=19,
        reconnect_attempts=5,
        reconnect_snapshots=5,
    )
    assert report["initial_snapshot_rate"] == 0.95
    assert report["reconnect_snapshot_rate"] == 1.0


def test_load_acceptance_requires_connection_frame_and_both_snapshot_rates():
    passing = {
        "failures": 0,
        "connection_success_rate": 0.99,
        "frame_delivery_rate": 0.99,
        "initial_snapshot_rate": 1.0,
        "reconnect_snapshot_rate": 1.0,
    }
    assert meets_acceptance_thresholds(passing, 0.99)

    for key in (
        "connection_success_rate",
        "frame_delivery_rate",
        "initial_snapshot_rate",
        "reconnect_snapshot_rate",
    ):
        failing = passing | {key: 0.98}
        assert not meets_acceptance_thresholds(failing, 0.99), key

    assert not meets_acceptance_thresholds(passing | {"failures": 1}, 0.99)


def test_fleet_event_validation_rejects_boolean_identity_and_coordinates():
    import json

    valid = {
        "telegram_id": 123,
        "latitude": 11.5,
        "longitude": 104.9,
        "recorded_at": "2026-11-12T00:00:00+00:00",
    }
    for field, value in (
        ("telegram_id", True),
        ("telegram_id", 0),
        ("latitude", True),
        ("latitude", 91),
        ("longitude", False),
        ("longitude", 181),
        ("recorded_at", ""),
    ):
        malformed = {**valid, field: value}
        assert not is_fleet_location_event(json.dumps(malformed)), (field, value)


def test_fleet_event_validation_rejects_non_finite_coordinates():
    import json

    event = {
        "telegram_id": 123,
        "latitude": float("nan"),
        "longitude": 104.9,
        "recorded_at": "2026-11-12T00:00:00+00:00",
    }
    assert not is_fleet_location_event(json.dumps(event))



def test_fleet_event_validation_rejects_unbounded_integer_coordinates():
    import json

    event = {
        "telegram_id": 123,
        "latitude": 10**1000,
        "longitude": 104.9,
        "recorded_at": "2026-11-12T00:00:00+00:00",
    }
    assert not is_fleet_location_event(json.dumps(event))



def test_build_report_does_not_treat_skipped_snapshot_probes_as_success():
    report = build_report(
        requested_connections=10,
        connected=10,
        messages_sent=1,
        frames_received=10,
        connect_latencies_ms=[10],
        fanout_latencies_ms=[5],
        failures=0,
        elapsed_seconds=1,
        initial_snapshot_attempts=0,
        initial_snapshots=0,
        reconnect_attempts=0,
        reconnect_snapshots=0,
    )
    assert report["initial_snapshot_rate"] == 0.0
    assert report["reconnect_snapshot_rate"] == 0.0
    assert not meets_acceptance_thresholds(report, 0.99)


def test_build_report_keeps_success_rate_when_all_snapshot_probes_pass():
    report = build_report(
        requested_connections=10,
        connected=10,
        messages_sent=1,
        frames_received=10,
        connect_latencies_ms=[10],
        fanout_latencies_ms=[5],
        failures=0,
        elapsed_seconds=1,
        initial_snapshot_attempts=10,
        initial_snapshots=10,
        reconnect_attempts=3,
        reconnect_snapshots=3,
    )
    assert report["initial_snapshot_rate"] == 1.0
    assert report["reconnect_snapshot_rate"] == 1.0
    assert meets_acceptance_thresholds(report, 0.99)


def test_acceptance_rejects_non_finite_or_out_of_range_rates():
    passing = {
        "failures": 0,
        "connection_success_rate": 1.0,
        "frame_delivery_rate": 1.0,
        "initial_snapshot_rate": 1.0,
        "reconnect_snapshot_rate": 1.0,
    }
    import math

    for value in (float("nan"), float("inf"), -0.01, 1.01, True, "1.0"):
        malformed = passing | {"frame_delivery_rate": value}
        assert not meets_acceptance_thresholds(malformed, 0.99), repr(value)


def test_acceptance_rejects_invalid_threshold_and_failure_count():
    passing = {
        "failures": 0,
        "connection_success_rate": 1.0,
        "frame_delivery_rate": 1.0,
        "initial_snapshot_rate": 1.0,
        "reconnect_snapshot_rate": 1.0,
    }
    import math

    for threshold in (float("nan"), float("inf"), -0.01, 1.01, True, "0.99"):
        assert not meets_acceptance_thresholds(passing, threshold), repr(threshold)
    for failures in (True, -1, 0.5, "0"):
        assert not meets_acceptance_thresholds(passing | {"failures": failures}, 0.99)


def test_load_acceptance_enforces_configured_connection_and_fanout_p95_ceilings():
    report = build_report(
        requested_connections=100,
        connected=100,
        messages_sent=3,
        frames_received=300,
        connect_latencies_ms=[10, 20, 30, 40, 50],
        fanout_latencies_ms=[5, 10, 15, 20, 25],
        failures=0,
        elapsed_seconds=2,
        initial_snapshot_attempts=100,
        initial_snapshots=100,
        reconnect_attempts=5,
        reconnect_snapshots=5,
    )
    assert meets_acceptance_thresholds(
        report, 0.99, max_connect_p95_ms=50, max_fanout_p95_ms=25
    )
    assert not meets_acceptance_thresholds(
        report, 0.99, max_connect_p95_ms=39
    )
    assert not meets_acceptance_thresholds(
        report, 0.99, max_fanout_p95_ms=24
    )


def test_load_acceptance_rejects_malformed_latency_percentiles_and_ceilings():
    report = {
        "failures": 0,
        "connection_success_rate": 1.0,
        "frame_delivery_rate": 1.0,
        "initial_snapshot_rate": 1.0,
        "reconnect_snapshot_rate": 1.0,
        "connect_latency_ms": {"p50": 10, "p95": 20, "p99": 30},
        "fanout_latency_ms": {"p50": 5, "p95": 10, "p99": 15},
    }
    import math

    for invalid in (True, float("nan"), float("inf"), 0, -1, "100"):
        assert not meets_acceptance_thresholds(
            report, 0.99, max_connect_p95_ms=invalid
        )
    for malformed in (
        {"p50": 10, "p95": 40, "p99": 30},
        {"p50": 10, "p95": float("nan"), "p99": 30},
        {"p50": 10, "p95": -1, "p99": 30},
        {"p50": 10, "p99": 30},
    ):
        assert not meets_acceptance_thresholds(
            report | {"fanout_latency_ms": malformed},
            0.99,
            max_fanout_p95_ms=100,
        )


def test_load_acceptance_keeps_latency_ceilings_opt_in():
    report = {
        "failures": 0,
        "connection_success_rate": 1.0,
        "frame_delivery_rate": 1.0,
        "initial_snapshot_rate": 1.0,
        "reconnect_snapshot_rate": 1.0,
    }
    # Legacy callers that only set delivery gates remain supported.
    assert meets_acceptance_thresholds(report, 0.99)
    # A configured latency ceiling requires a real measured distribution.
    assert not meets_acceptance_thresholds(
        report, 0.99, max_connect_p95_ms=100
    )


def test_fleet_event_validation_requires_the_expected_gps_sample_when_requested():
    import json

    event = {
        "telegram_id": 123,
        "latitude": 11.5,
        "longitude": 104.9,
        "recorded_at": "2026-11-12T00:00:00+00:00",
    }
    encoded = json.dumps(event)
    assert is_fleet_location_event(
        encoded, expected_recorded_at=event["recorded_at"]
    )
    assert not is_fleet_location_event(
        encoded, expected_recorded_at="2026-11-12T00:00:01+00:00"
    )
