import pytest

from firetv.config import Config, DEFAULT_INPUTS, parse_inputs


def test_parse_inputs_basic():
    assert parse_inputs("Fire TV=HOME,HDMI 1=HDMI1") == [
        ("Fire TV", "HOME"),
        ("HDMI 1", "HDMI1"),
    ]


def test_parse_inputs_strips_whitespace():
    assert parse_inputs(" Fire TV = HOME , HDMI 1 = HDMI1 ") == [
        ("Fire TV", "HOME"),
        ("HDMI 1", "HDMI1"),
    ]


def test_parse_inputs_rejects_missing_equals():
    with pytest.raises(ValueError):
        parse_inputs("Fire TV")


def test_from_env_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("FIRETV_HOST", "10.0.0.5")
    monkeypatch.setenv("FIRETV_STATE_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.host == "10.0.0.5"
    assert cfg.port == 5555
    assert cfg.name == "Fire TV"
    assert cfg.hap_port == 51828
    assert cfg.poll_seconds == 15
    assert cfg.inputs == parse_inputs(DEFAULT_INPUTS)
    assert cfg.inputs[0] == ("Fire TV", "HOME")
    assert len(cfg.inputs) == 5


def test_from_env_requires_host(monkeypatch):
    monkeypatch.delenv("FIRETV_HOST", raising=False)
    with pytest.raises(SystemExit):
        Config.from_env()
