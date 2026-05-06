"""SP-7 Phase F2: Prometheus config smoke tests.

Validates infra/prometheus/prometheus.yml + alert_rules.yml parse cleanly
as YAML and contain the expected scrape jobs / alert rule.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _infra_root() -> Path:
    # backend/tests/unit/test_infra_prometheus.py → repo_root/infra/prometheus
    return Path(__file__).resolve().parents[3] / "infra" / "prometheus"


def test_prometheus_yml_parses_as_yaml() -> None:
    data = yaml.safe_load((_infra_root() / "prometheus.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "scrape_configs" in data
    assert data["global"]["scrape_interval"] == "15s"


def test_prometheus_scrapes_backend_and_loki_and_self() -> None:
    data = yaml.safe_load((_infra_root() / "prometheus.yml").read_text(encoding="utf-8"))
    job_names = {job["job_name"] for job in data["scrape_configs"]}
    assert "trading-radar-backend" in job_names
    assert "loki" in job_names
    assert "prometheus" in job_names

    backend_job = next(j for j in data["scrape_configs"] if j["job_name"] == "trading-radar-backend")
    targets = backend_job["static_configs"][0]["targets"]
    assert "backend:8000" in targets


def test_alert_rules_yml_parses_as_yaml() -> None:
    data = yaml.safe_load((_infra_root() / "alert_rules.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "groups" in data


def test_high_latency_alert_present_with_correct_threshold() -> None:
    data = yaml.safe_load((_infra_root() / "alert_rules.yml").read_text(encoding="utf-8"))
    rules = [r for grp in data["groups"] for r in grp.get("rules", [])]
    high_latency = [r for r in rules if r.get("alert") == "HighLatency"]
    assert len(high_latency) == 1
    rule = high_latency[0]
    # Spec §5.18: p99 > 500ms over 5min, fires after 5min.
    assert "histogram_quantile(0.99" in rule["expr"]
    assert "http_request_duration_seconds_bucket[5m]" in rule["expr"]
    assert "> 0.5" in rule["expr"]
    assert rule["for"] == "5m"
