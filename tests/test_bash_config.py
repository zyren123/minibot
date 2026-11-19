import pytest

from src.minibot.config import BashConfig, Config, ToolsConfig
from src.minibot.config.settings import _parse_tools_config, _DEFAULT_CONFIG_TEMPLATE
from src.minibot.tools.builtin.bash import BashTool
import yaml


def test_defaults_preserve_local_and_templates_agree():
    assert Config().tools.bash == BashConfig()
    assert _parse_tools_config(yaml.safe_load(_DEFAULT_CONFIG_TEMPLATE)["tools"]).bash == BashConfig()
    assert ToolsConfig(bash=BashConfig(backend="disabled")).bash.backend == "disabled"


@pytest.mark.parametrize("kwargs", [
    {"backend": "remote"}, {"backend": []}, {"image": ""}, {"image": None}, {"image": "-bad"},
    {"memory_mb": 0}, {"memory_mb": 1.1}, {"memory_mb": True}, {"memory_mb": "12"},
    {"pids_limit": -1}, {"pids_limit": False}, {"cpus": 0}, {"cpus": "1"},
    {"cpus": True}, {"cpus": float("nan")}, {"cpus": float("inf")},
])
def test_invalid_sdk_and_yaml_values(kwargs):
    with pytest.raises(ValueError):
        BashConfig(**kwargs)
    with pytest.raises(ValueError):
        _parse_tools_config({"bash": kwargs})


@pytest.mark.parametrize("value", [None, "docker", [], {"bash": None}, {"bash": "docker"}, {"bash": []}, {"bash": {"network": "host"}}])
def test_yaml_boundary_rejects_malformed_or_unknown_bash(value):
    with pytest.raises(ValueError):
        _parse_tools_config(value)


def test_sdk_requires_typed_bash_config():
    with pytest.raises(ValueError):
        ToolsConfig(bash={"backend": "docker"})


@pytest.mark.parametrize("timeout", [
    None, float("inf"), float("-inf"), float("nan"), 0, -1, True, False, "2", [],
])
@pytest.mark.parametrize("boundary", ["sdk", "yaml", "direct"])
def test_timeout_requires_positive_finite_number(tmp_path, timeout, boundary):
    with pytest.raises(ValueError, match="positive finite number"):
        if boundary == "sdk":
            ToolsConfig(timeout=timeout)
        elif boundary == "yaml":
            _parse_tools_config(yaml.safe_load(yaml.safe_dump({"timeout": timeout})))
        else:
            BashTool(tmp_path, timeout=timeout)


@pytest.mark.parametrize("timeout", [1, 0.25])
def test_timeout_preserves_positive_integer_and_fraction(tmp_path, timeout):
    assert ToolsConfig(timeout=timeout).timeout == timeout
    assert _parse_tools_config(yaml.safe_load(yaml.safe_dump({"timeout": timeout}))).timeout == timeout
    assert BashTool(tmp_path, timeout=timeout).timeout == timeout
