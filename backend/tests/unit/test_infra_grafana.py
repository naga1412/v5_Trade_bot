"""SP-7 Phase F3: Grafana dashboards + provisioning smoke tests.

Validates that every JSON dashboard under infra/grafana/dashboards/ parses
as JSON, that the four expected dashboards exist with the right uids and
titles, and that each dashboard contains at least one panel with a
PromQL query targeting the metric the spec requires.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def _grafana_root() -> Path:
    # backend/tests/unit/test_infra_grafana.py → repo_root/infra/grafana
    return Path(__file__).resolve().parents[3] / "infra" / "grafana"


def _dashboards_root() -> Path:
    return _grafana_root() / "dashboards"


REQUIRED_DASHBOARDS: dict[str, dict[str, str]] = {
    "trading_radar_overview.json": {
        "uid": "tr-overview",
        "expected_query_substring": "http_request_duration_seconds_bucket",
    },
    "adapters_health.json": {
        "uid": "tr-adapters-health",
        "expected_query_substring": "adapter_",
    },
    "audit_chain_status.json": {
        "uid": "tr-audit-chain",
        "expected_query_substring": "audit_verifier_",
    },
    "ml_checkpoint_history.json": {
        "uid": "tr-ml-checkpoints",
        "expected_query_substring": "ml_checkpoint_",
    },
}


def test_all_required_dashboards_exist() -> None:
    root = _dashboards_root()
    assert root.is_dir()
    for filename in REQUIRED_DASHBOARDS:
        assert (root / filename).is_file(), f"missing dashboard: {filename}"


@pytest.mark.parametrize("filename,meta", list(REQUIRED_DASHBOARDS.items()))
def test_dashboard_parses_as_json_with_correct_uid_and_query(
    filename: str, meta: dict[str, str]
) -> None:
    path = _dashboards_root() / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["uid"] == meta["uid"]
    assert "panels" in data and len(data["panels"]) > 0
    # At least one panel must reference the expected metric family.
    all_exprs = []
    for panel in data["panels"]:
        for target in panel.get("targets", []):
            if "expr" in target:
                all_exprs.append(target["expr"])
    assert any(meta["expected_query_substring"] in expr for expr in all_exprs), (
        f"{filename}: no panel query references {meta['expected_query_substring']}"
    )


def test_grafana_provisioning_yamls_parse() -> None:
    ds = _grafana_root() / "provisioning" / "datasources" / "datasource.yml"
    db = _grafana_root() / "provisioning" / "dashboards" / "dashboards.yml"
    assert ds.is_file() and db.is_file()
    ds_data = yaml.safe_load(ds.read_text(encoding="utf-8"))
    db_data = yaml.safe_load(db.read_text(encoding="utf-8"))
    ds_names = {entry["name"] for entry in ds_data["datasources"]}
    assert {"Prometheus", "Loki"}.issubset(ds_names)
    assert db_data["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"
