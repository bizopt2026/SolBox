import pytest

from app.config import load_settings


def test_load_settings_resolves_data_dir_relative_to_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_dir = "data"\nsecret_key = "k"\n', encoding="utf-8")
    s = load_settings(cfg)
    assert s.data_dir == tmp_path / "data"
    assert s.db_path == tmp_path / "data" / "app.db"
    assert s.knowledge_dir == tmp_path / "data" / "knowledge"
    assert s.ai_concurrency == 1 and s.ai_timeout_s == 600


@pytest.mark.parametrize("key", ['""', '"change-me"', '"  "'])
def test_weak_secret_key_exits(tmp_path, key):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'data_dir = "data"\nsecret_key = {key}\n', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        load_settings(cfg)
    assert "secret_key" in str(e.value) and "token_urlsafe" in str(e.value)


def test_utf8_bom_config_loads(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'\xef\xbb\xbfdata_dir = "data"\nsecret_key = "k"\n')
    assert load_settings(cfg).data_dir == tmp_path / "data"


def test_backslash_windows_path_exits_with_hint(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_dir = "C:\\solbox\\data"\nsecret_key = "k"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        load_settings(cfg)
    assert "작은따옴표" in str(e.value) and "/" in str(e.value)
