from scripts.load_test_locations import build_report, percentile


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
    assert report["frame_delivery_rate"] == 270 / 285
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
