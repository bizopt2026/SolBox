"""설정 파일(TOML) 읽기."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    secret_key: str
    host: str = "127.0.0.1"
    port: int = 8443
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    claude_cmd: list[str] = field(default_factory=lambda: ["claude"])
    ai_concurrency: int = 1
    ai_timeout_s: int = 600
    research_timeout_s: int = 1800
    ai_worker_enabled: bool = True
    max_upload_mb: int = 100

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    @property
    def research_work_dir(self) -> Path:
        return self.data_dir / "research_work"

    @property
    def decks_dir(self) -> Path:
        return self.data_dir / "decks"


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        env = os.environ.get("SOLBOX_CONFIG")
        path = Path(env) if env else Path("config.toml")
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"설정 파일을 찾을 수 없습니다: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(
            f"설정 파일을 읽을 수 없습니다: {path} ({e})\n"
            "Windows 경로는 C:/solbox/data 처럼 / 로 쓰거나 'C:\\solbox\\data' 처럼 작은따옴표로 감싸세요."
        ) from None
    known = {f.name for f in fields(Settings)}
    unknown = set(raw) - known
    if unknown:
        raise SystemExit(f"알 수 없는 설정 키: {', '.join(sorted(unknown))}")
    for key in ("data_dir", "secret_key"):
        if key not in raw:
            raise SystemExit(f"설정 파일에 '{key}' 값이 없습니다: {path}")
    if str(raw["secret_key"]).strip() in ("", "change-me"):
        raise SystemExit(
            f"설정 파일의 'secret_key' 를 길고 무작위한 값으로 바꾸세요: {path}\n"
            'python -c "import secrets; print(secrets.token_urlsafe(32))" 로 만들 수 있습니다.'
        )
    data_dir = Path(raw["data_dir"])
    if not data_dir.is_absolute():
        data_dir = path.resolve().parent / data_dir
    raw["data_dir"] = data_dir
    return Settings(**raw)
