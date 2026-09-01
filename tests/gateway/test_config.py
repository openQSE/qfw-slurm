from pathlib import Path

import pytest

from qfw_slurm_gateway.config import ConfigurationError, load_config


CONFIG = """
listen:
  host: 127.0.0.1
  port: 18095
  max-credential-bytes: 65536
  request-timeout-seconds: 120
authentication:
  mechanism: munge
  accepted-uids: [0]
  expected-plugin-name: spank_quantum
slurm:
  cluster-name: qfw-cluster
  verifier: scontrol-json
qfw:
  activation: ${QFW_PREFIX}/bin/qfw-activate
  venv: /opt/openqse/qfw-venv
  site-config: /etc/openqse/qfw/site.yaml
journal:
  path: /var/lib/qfw-slurm-gateway/reservations.sqlite3
"""


def test_load_config_expands_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QFW_PREFIX", "/opt/openqse/qfw")
    path = tmp_path / "gateway.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    config = load_config(path, validate_permissions=False)
    assert config.listen_port == 18095
    assert config.qfw_activation == Path("/opt/openqse/qfw/bin/qfw-activate")
    assert config.accepted_uids == {0}


def test_config_rejects_implicit_authentication(tmp_path) -> None:
    path = tmp_path / "gateway.yaml"
    path.write_text(CONFIG.replace("mechanism: munge", "mechanism: none"))
    with pytest.raises(ConfigurationError, match="must be 'munge'"):
        load_config(path, validate_permissions=False)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        (
            "verifier: scontrol-json",
            "verifier: deterministic",
            "must be 'scontrol-json'",
        ),
        (
            "qfw:\n",
            "qfw:\n  adapter: deterministic\n",
            "not a production gateway configuration field",
        ),
    ),
)
def test_config_rejects_test_implementations(
    tmp_path, old, new, message
) -> None:
    path = tmp_path / "gateway.yaml"
    path.write_text(CONFIG.replace(old, new), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_config(path, validate_permissions=False)
