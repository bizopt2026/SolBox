"""음성 받아쓰기 연결 지점. 지금은 엔진이 없어(`none`) 음성은 "받아쓰기 필요"로만 둔다.

엔진을 붙일 때는 `Transcriber` 를 구현하고 `TRANSCRIBERS` 에 이름을 더한다 (설정 `transcriber` 값)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings


class TranscribeError(Exception):
    """받아쓰기에 실패했다. 메시지는 화면에 그대로 보여도 되는 한국어 안내."""


class Transcriber:
    def available(self) -> bool:
        raise NotImplementedError

    def transcribe(self, path: Path) -> str:
        """음성 파일의 글. 실패하면 `TranscribeError`."""
        raise NotImplementedError

    def label(self) -> str:
        """점검 화면에 보일 상태 문구."""
        return "사용 중" if self.available() else "사용 안 함"


class NoneTranscriber(Transcriber):
    def available(self) -> bool:
        return False

    def transcribe(self, path: Path) -> str:
        raise TranscribeError("음성 받아쓰기를 사용하지 않도록 설정되어 있습니다.")


TRANSCRIBERS: dict[str, type[Transcriber]] = {"none": NoneTranscriber}


def get(settings: "Settings") -> Transcriber:
    return TRANSCRIBERS[settings.transcriber]()


def label(settings: "Settings") -> str:
    return get(settings).label()
