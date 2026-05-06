"""SP-7 Phase F5: Loki + Promtail config smoke tests.

Validates infra/loki/loki.yml + infra/promtail/promtail.yml parse cleanly
as YAML and contain the wiring the F1 docker-compose stack expects
(Loki HTTP listener on 3100, Promtail pushes to loki:3100).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _infra_root() -> Path:
    # backend/tests/unit/test_infra_loki_promtail.py → repo_root/infra
    return Path(__file__).resolve().parents[3] / "infra"


def test_loki_yml_parses_and_listens_on_3100() -> None:
    data = yaml.safe_load((_infra_root() / "loki" / "loki.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["server"]["http_listen_port"] == 3100
    # Filesystem storage is the F5 minimal default; operator promotes to
    # S3-compatible storage in production by editing storage_config.
    assert "filesystem" in data["storage_config"]


def test_promtail_yml_parses_and_pushes_to_loki_sidecar() -> None:
    data = yaml.safe_load((_infra_root() / "promtail" / "promtail.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    clients = data["clients"]
    assert any("loki:3100" in c["url"] for c in clients)


def test_promtail_tails_var_log_glob() -> None:
    data = yaml.safe_load((_infra_root() / "promtail" / "promtail.yml").read_text(encoding="utf-8"))
    jobs = data["scrape_configs"]
    paths = [
        cfg["labels"]["__path__"]
        for job in jobs
        for cfg in job["static_configs"]
        if "__path__" in cfg.get("labels", {})
    ]
    assert any("/var/log/" in p for p in paths)
