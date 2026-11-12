from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_prometheus_alert_rules_parse_and_cover_redis_pool_and_outbox():
    document = yaml.safe_load(
        (ROOT / "deploy/monitoring/vcar-alert-rules.yml").read_text(encoding="utf-8")
    )
    rules = [
        rule
        for group in document["groups"]
        for rule in group["rules"]
    ]
    by_name = {rule["alert"]: rule for rule in rules}

    assert {
        "VcarMetricsTargetDown",
        "VcarRedisUnavailable",
        "VcarDatabasePoolSaturated",
        "VcarOrderOutboxBacklog",
        "VcarOrderOutboxDeliveryStalled",
    } <= by_name.keys()
    assert "vcar_redis_available" in by_name["VcarRedisUnavailable"]["expr"]
    assert "vcar_database_pool_max_connections" in by_name["VcarDatabasePoolSaturated"]["expr"]
    assert "vcar_database_pool_idle_connections" in by_name["VcarDatabasePoolSaturated"]["expr"]


def test_prometheus_scrape_config_uses_private_metrics_token_file():
    document = yaml.safe_load(
        (ROOT / "deploy/monitoring/prometheus-vcar-scrape.yml").read_text(encoding="utf-8")
    )
    app_job = next(job for job in document["scrape_configs"] if job["job_name"] == "vcar-app")
    assert app_job["metrics_path"] == "/metrics"
    assert app_job["bearer_token_file"] == "/etc/prometheus/secrets/vcar-metrics-token"


def test_host_resource_alerts_use_node_exporter_metrics():
    rules_doc = yaml.safe_load(
        (ROOT / "deploy/monitoring/vcar-alert-rules.yml").read_text(encoding="utf-8")
    )
    rules = {
        rule["alert"]: rule
        for group in rules_doc["groups"]
        for rule in group["rules"]
    }
    assert {
        "VcarHostCpuSaturated",
        "VcarHostMemoryPressure",
        "VcarHostRootFilesystemLow",
    } <= rules.keys()
    assert "node_cpu_seconds_total" in rules["VcarHostCpuSaturated"]["expr"]
    assert "node_memory_MemAvailable_bytes" in rules["VcarHostMemoryPressure"]["expr"]
    assert 'mountpoint="/"' in rules["VcarHostRootFilesystemLow"]["expr"]


def test_node_exporter_scrape_is_separate_and_private_loopback_target():
    document = yaml.safe_load(
        (ROOT / "deploy/monitoring/prometheus-vcar-scrape.yml").read_text(encoding="utf-8")
    )
    node_job = next(job for job in document["scrape_configs"] if job["job_name"] == "vcar-node")
    assert node_job["static_configs"][0]["targets"] == ["127.0.0.1:9100"]
