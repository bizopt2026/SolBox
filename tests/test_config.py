from app.config import load_settings


def test_load_settings_resolves_data_dir_relative_to_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_dir = "data"\nsecret_key = "k"\n', encoding="utf-8")
    s = load_settings(cfg)
    assert s.data_dir == tmp_path / "data"
    assert s.db_path == tmp_path / "data" / "app.db"
    assert s.knowledge_dir == tmp_path / "data" / "knowledge"
    assert s.ai_concurrency == 1 and s.ai_timeout_s == 600
