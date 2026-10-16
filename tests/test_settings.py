"""API settings tests use disposable directories and invented credentials only."""
import base64
import json

import pytest

from galchat.settings import DEFAULTS, Settings


FAKE_KEY = "sk-nextline-unit-test-not-a-real-key"


def _dpapi():
    # Exercise real Windows encryption when available; never read user settings.
    return pytest.importorskip("win32crypt")


def test_dpapi_round_trip_keeps_credentials_out_of_disk_and_public_state(tmp_path):
    crypt = _dpapi()
    settings = Settings(tmp_path)
    public = settings.save({"api_key": FAKE_KEY})
    disk = settings.path.read_text(encoding="utf-8")
    stored = json.loads(disk)
    encrypted = base64.b64decode(stored["protected_key"])

    assert FAKE_KEY not in disk
    assert "api_key" not in stored
    assert encrypted != FAKE_KEY.encode()
    assert FAKE_KEY.encode() not in encrypted
    assert crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode() == FAKE_KEY
    assert "protected_key" not in public
    assert "api_key" not in public
    assert FAKE_KEY not in json.dumps(public)
    assert stored["protected_key"] not in json.dumps(public)
    assert public["api_key_set"] and public["api_ready"]
    assert "api_key" not in settings.values

    restarted = Settings(tmp_path)
    assert restarted.model_config()["api_key"] == FAKE_KEY
    assert "protected_key" not in restarted.model_config()
    assert "protected_key" not in restarted.public()


@pytest.mark.parametrize("empty_key", ["", " \t\n ", None])
def test_blank_key_preserves_existing_encrypted_key(tmp_path, empty_key):
    _dpapi()
    settings = Settings(tmp_path)
    settings.save({"api_key": FAKE_KEY})
    previous = settings.values["protected_key"]

    settings.save({"api_key": empty_key, "follow_window": False})

    assert settings.values["protected_key"] == previous
    assert Settings(tmp_path).model_config()["api_key"] == FAKE_KEY
    assert settings.public()["follow_window"] is False


def test_explicit_key_clear_removes_credential_and_ready_state(tmp_path):
    _dpapi()
    settings = Settings(tmp_path)
    settings.save({"api_key": FAKE_KEY})
    public = settings.save({"clear_api_key": True})

    assert not public["api_key_set"]
    assert not public["api_ready"]
    assert "protected_key" not in json.loads(settings.path.read_text(encoding="utf-8"))
    assert Settings(tmp_path).model_config()["api_key"] == ""


@pytest.mark.parametrize("old_endpoint", ["http://127.0.0.1:18765", "http://127.0.0.1:18765/v1"])
def test_only_abandoned_keyless_local_defaults_migrate_to_deepseek(tmp_path, old_endpoint):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"base_url": old_endpoint, "model": "qwen3-4b",
                                "follow_window": False, "auto_generate": False, "timeout": 33}), encoding="utf-8")

    settings = Settings(tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["base_url"] == settings.values["base_url"] == DEFAULTS["base_url"]
    assert saved["model"] == settings.values["model"] == DEFAULTS["model"]
    assert saved["follow_window"] is False
    assert saved["auto_generate"] is False
    assert saved["timeout"] == 33
    assert not settings.public()["api_ready"]


@pytest.mark.parametrize("endpoint,model", [
    ("https://custom.example.test/v1", "qwen3-4b"),
    ("http://127.0.0.1:18765/v1", "my-custom-model"),
    ("http://localhost:4321/v1", "qwen3-4b"),
    ("https://api.deepseek.com/v1", "custom-deepseek-model"),
])
def test_custom_provider_or_model_configuration_is_not_overwritten(tmp_path, endpoint, model):
    settings = Settings(tmp_path)
    settings.save({"base_url": endpoint, "model": model, "auto_generate": False})

    restored = Settings(tmp_path)

    assert restored.values["base_url"] == endpoint
    assert restored.values["model"] == model
    assert restored.values["auto_generate"] is False


def test_credentialed_old_local_configuration_is_not_treated_as_abandoned(tmp_path):
    _dpapi()
    settings = Settings(tmp_path)
    settings.save({"base_url": "http://127.0.0.1:18765/v1", "model": "qwen3-4b", "api_key": FAKE_KEY})

    restored = Settings(tmp_path)

    assert restored.values["base_url"] == "http://127.0.0.1:18765/v1"
    assert restored.values["model"] == "qwen3-4b"
    assert restored.model_config()["api_key"] == FAKE_KEY


@pytest.mark.parametrize("endpoint", ["https://api.deepseek.com", "https://api.deepseek.com/v1", "https://API.DEEPSEEK.COM/v1"])
def test_deepseek_official_hostname_receives_non_thinking_option(tmp_path, endpoint):
    settings = Settings(tmp_path)
    settings.save({"base_url": endpoint})

    assert settings.model_config()["provider_extra"] == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("endpoint", [
    "https://api.deepseek.com.example.test/v1",
    "https://proxy.example.test/api.deepseek.com",
    "https://deepseek.com/v1",
    "http://127.0.0.1:9999/v1",
    "https://other.example.test/v1",
])
def test_other_hosts_do_not_receive_deepseek_specific_options(tmp_path, endpoint):
    settings = Settings(tmp_path)
    settings.save({"base_url": endpoint})

    assert "provider_extra" not in settings.model_config()


def test_unrecognized_plaintext_fields_are_not_loaded_or_republished(tmp_path):
    # This intentionally malformed fixture is not a real settings file. It
    # confirms older or external data cannot introduce a plaintext public key.
    (tmp_path / "settings.json").write_text(json.dumps({"api_key": FAKE_KEY,
        "provider_extra": {"thinking": {"type": "enabled"}}}), encoding="utf-8")

    settings = Settings(tmp_path)
    public = settings.save({})

    assert FAKE_KEY not in json.dumps(public)
    assert "api_key" not in settings.values
    assert "provider_extra" not in settings.values
    assert FAKE_KEY not in settings.path.read_text(encoding="utf-8")


def test_visual_calibration_survives_restart_but_never_becomes_api_config(tmp_path):
    settings = Settings(tmp_path)
    calibration = {"width": 700, "height": 600, "account_name": "测试账号",
                   "region": [280, 20, 690, 590], "title_region": [300, 30, 500, 70],
                   "input_region": [300, 440, 680, 570], "unexpected": "not persisted"}
    settings.save({"visual_config": calibration})
    restored = Settings(tmp_path)
    assert restored.public()["visual_config"]["account_name"] == "测试账号"
    assert "unexpected" not in restored.public()["visual_config"]
    assert "visual_config" not in restored.model_config()
