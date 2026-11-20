import pytest

from config import ConfigError, load_config, parse_config

VALID = {"targets": [{"name": "a", "url": "http://a:8000/health"}]}


def test_defaults_and_container_falls_back_to_name():
    cfg = parse_config(VALID, env={})
    t = cfg.targets[0]
    assert (t.container, t.interval_s, t.failure_threshold) == ("a", 5.0, 3)
    assert cfg.escalation.ec2_reboot_enabled is False
    assert cfg.recovery.max_restarts == 3


def test_env_can_enable_level2_escalation():
    assert parse_config(VALID, env={"ESCALATION_EC2_REBOOT": "true"}).escalation.ec2_reboot_enabled is True
    data = {**VALID, "escalation": {"ec2_reboot": {"enabled": True}}}
    assert parse_config(data, env={"ESCALATION_EC2_REBOOT": "false"}).escalation.ec2_reboot_enabled is False


@pytest.mark.parametrize(
    "mutation",
    [
        {"targets": []},
        {"targets": [{"name": "a"}]},  # no url
        {"targets": [{"name": "a", "url": "ftp://x/health"}]},
        {"targets": [{"name": "bad name!", "url": "http://x/h"}]},
        {"targets": [{"name": "a", "url": "http://x/h", "interval_s": 0}]},
        {"targets": [{"name": "a", "url": "http://x/h", "failure_threshold": 0}]},
        {"targets": [{"name": "a", "url": "http://x/h", "timeout_s": True}]},
        {"targets": [{"name": "a", "url": "http://x/h"}, {"name": "a", "url": "http://y/h"}]},
        {"targets": VALID["targets"], "recovery": {"max_restarts": 0}},
        {"targets": VALID["targets"], "recovery": {"cooldown_s": -1}},
    ],
)
def test_invalid_configuration_is_rejected(mutation):
    with pytest.raises(ConfigError):
        parse_config(mutation, env={})


def test_root_must_be_a_mapping():
    with pytest.raises(ConfigError):
        parse_config(["nope"], env={})


def test_load_config_errors_are_clean(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml", env={})
    bad = tmp_path / "bad.yaml"
    bad.write_text("targets: [unclosed")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad, env={})


def test_shipped_config_is_valid():
    cfg = load_config("monitor/config.yaml", env={})
    assert cfg.targets[0].container == "demo-service"
