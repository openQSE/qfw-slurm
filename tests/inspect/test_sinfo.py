from __future__ import annotations

import json
import sys
import types

import pytest

from qfw_slurm_inspect.models import QPMService, SlurmNode
from qfw_slurm_inspect.sinfo import (
    _configured_from_nodes,
    _configured_services,
    _rows,
    main,
)


def test_associates_qpm_with_head_and_dvm_workers() -> None:
    nodes = [
        SlurmNode("nwqsim-head", "IDLE", "qfw-services", ("qpm",)),
        SlurmNode("nwqsim-worker-1", "IDLE", "qfw-services", ("dvm",)),
    ]
    service = QPMService(
        "nwqsim", "runtime", 2, "nwqsim", "BUSY", True, 2, 1,
        ("nwqsim-head", "nwqsim-worker-1"), True,
    )

    rows = _rows(nodes, [service])

    assert [row["node"] for row in rows] == [
        "nwqsim-head", "nwqsim-worker-1"
    ]
    assert all(row["state"] == "BUSY" for row in rows)
    assert all(row["active_reservations"] == 2 for row in rows)


def test_unready_service_is_down() -> None:
    service = QPMService(
        "iqm", "runtime", 1, "iqm", "IDLE", False, 0, 0,
        ("iqm-head",), None, "unreachable",
    )

    row = _rows([], [service])[0]

    assert row["state"] == "DOWN"
    assert row["slurm_state"] == "UNKNOWN"


def test_maintenance_service_remains_distinct_from_down() -> None:
    service = QPMService(
        "iqm", "runtime", 1, "iqm", "MAINT", False, 0, 0,
        ("iqm-head",), None,
    )

    row = _rows([], [service])[0]

    assert row["state"] == "MAINT"


def test_configured_absent_service_is_derived_from_node_features() -> None:
    nodes = [
        SlurmNode(
            "nwqsim-head", "IDLE", "qfw-services",
            ("qfw-service", "qpm-nwqsim"),
        ),
        SlurmNode(
            "nwqsim-worker-1", "IDLE", "qfw-services",
            ("qfw-service", "qpm-nwqsim"),
        ),
    ]

    service = _configured_from_nodes(nodes)[0]

    assert service.service_id == "nwqsim"
    assert service.state == "DOWN"
    assert service.assigned_hosts == ("nwqsim-head", "nwqsim-worker-1")


def test_slurm_features_override_general_qfw_manifest(monkeypatch) -> None:
    nodes = [
        SlurmNode(
            "iqm-head", "IDLE", "qfw-services",
            ("qfw-service", "qpm-iqm-ornl-20q"),
        )
    ]
    config = types.ModuleType("qfw_runtime.config")
    config.expand_config_value = lambda value: value
    config.load_service_manifest = lambda _path: [
        {"name": "fake-iqm", "assigned-hosts": "group1"},
    ]
    config.load_yaml = lambda _path: {}
    config.resolve_site_config = lambda _path: "/site.yaml"
    config.site_service_config = lambda *_args, **_kwargs: {
        "manifest": "/manifest.yaml"
    }
    package = types.ModuleType("qfw_runtime")
    package.config = config
    monkeypatch.setitem(sys.modules, "qfw_runtime", package)
    monkeypatch.setitem(sys.modules, "qfw_runtime.config", config)

    services = _configured_services(None, nodes)

    assert [service.service_id for service in services] == ["iqm-ornl-20q"]
    assert services[0].assigned_hosts == ("iqm-head",)


def test_json_and_node_filter(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "qfw_slurm_inspect.sinfo.SlurmJsonClient.nodes",
        lambda self: [SlurmNode("iqm-head", "IDLE")],
    )
    monkeypatch.setattr(
        "qfw_slurm_inspect.sinfo.QPMInspectionClient.connect",
        lambda timeout: type("Client", (), {
            "services": lambda self: [QPMService(
                "iqm", "run", 4, "IQM", "IDLE", True, 0, 0,
                ("iqm-head",),
            )]
        })(),
    )

    assert main(["--json", "iqm-head"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "qfw-sinfo-v1"
    assert payload["services"][0]["generation"] == 4


def test_unknown_node_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "qfw_slurm_inspect.sinfo.SlurmJsonClient.nodes",
        lambda self: [],
    )
    monkeypatch.setattr(
        "qfw_slurm_inspect.sinfo.QPMInspectionClient.connect",
        lambda timeout: type("Client", (), {"services": lambda self: []})(),
    )

    with pytest.raises(SystemExit, match="unknown service node"):
        main(["missing"])
