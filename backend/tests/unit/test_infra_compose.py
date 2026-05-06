"""SP-7 Phase F1: docker-compose.yml monitoring services smoke test.

Validates that the top-level docker-compose.yml parses cleanly as YAML and
contains the four monitoring services (prometheus, grafana, loki, promtail)
plus the named volumes (prometheus_data, grafana_data) that Phase F1 added.

This is a config-only smoke test — we never actually start the stack from
inside the test runner.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _compose_path() -> Path:
    # backend/tests/unit/test_infra_compose.py → repo_root/docker-compose.yml
    return Path(__file__).resolve().parents[3] / "docker-compose.yml"


def test_docker_compose_parses_as_yaml() -> None:
    path = _compose_path()
    assert path.is_file(), f"missing docker-compose.yml at {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "services" in data


def test_docker_compose_declares_monitoring_services() -> None:
    data = yaml.safe_load(_compose_path().read_text(encoding="utf-8"))
    services = data["services"]
    for name in ("prometheus", "grafana", "loki", "promtail"):
        assert name in services, f"missing monitoring service: {name}"


def test_prometheus_service_uses_pinned_image_and_mounts_config() -> None:
    services = yaml.safe_load(_compose_path().read_text(encoding="utf-8"))["services"]
    prom = services["prometheus"]
    assert prom["image"] == "prom/prometheus:v2.55.0"
    # Must mount infra/prometheus/prometheus.yml read-only somewhere.
    mounts = " ".join(prom.get("volumes", []))
    assert "infra/prometheus/prometheus.yml" in mounts


def test_grafana_service_uses_admin_password_env() -> None:
    services = yaml.safe_load(_compose_path().read_text(encoding="utf-8"))["services"]
    grafana = services["grafana"]
    assert grafana["image"] == "grafana/grafana:11.2.0"
    env = grafana.get("environment", {})
    # Password must be sourced from env (not hardcoded).
    assert env.get("GF_SECURITY_ADMIN_PASSWORD") == "${GRAFANA_ADMIN_PASSWORD}"


def test_loki_and_promtail_pinned_to_3_2_0() -> None:
    services = yaml.safe_load(_compose_path().read_text(encoding="utf-8"))["services"]
    assert services["loki"]["image"] == "grafana/loki:3.2.0"
    assert services["promtail"]["image"] == "grafana/promtail:3.2.0"


def test_named_volumes_for_prometheus_and_grafana_declared() -> None:
    data = yaml.safe_load(_compose_path().read_text(encoding="utf-8"))
    volumes = data.get("volumes", {}) or {}
    assert "prometheus_data" in volumes
    assert "grafana_data" in volumes


def test_env_example_documents_grafana_admin_password() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    env_example = repo_root / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    assert "GRAFANA_ADMIN_PASSWORD=" in text
