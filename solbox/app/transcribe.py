"""음성 받아쓰기 연결 지점과 서버 안에서 도는 엔진(faster-whisper).

엔진은 `Transcriber` 를 구현하고 `TRANSCRIBERS` 에 이름을 더한다 (설정 `transcriber` 값).
faster-whisper 는 선택 설치(`pip install -e ".[stt]"`)라서 이 모듈을 불러올 때는 가져오지 않고, 쓸 때 처음 가져온다.
받아쓰기는 프로세스 전체에서 한 번에 하나만 돈다 (`run`)."""
from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import threading
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

MODEL_NAMES = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
INSTALL_HINT = 'faster-whisper 미설치: pip install -e ".[stt]"'
SETUP_HINT = "python -m app.cli stt-setup"
MARKER_EVERY_S = 60

_RUN_LOCK = threading.Lock()  # 받아쓰기는 프로세스 전체에서 한 번에 하나만


class TranscribeError(Exception):
    """받아쓰기에 실패했다. 메시지는 화면에 그대로 보여도 되는 한국어 안내."""


class TranscribeCancelled(Exception):
    """진행 알림 쪽에서 "이 받아쓰기는 이제 필요 없다"고 알릴 때 던진다 (묶음을 버린 경우 등). 엔진은 그대로 위로 올린다."""


ProgressCallback = Callable[[float], None]


class Transcriber:
    last_audio_seconds: float | None = None

    @classmethod
    def from_settings(cls, settings: "Settings") -> "Transcriber":
        return cls()

    def available(self) -> bool:
        raise NotImplementedError

    def transcribe(self, path: Path, *, hints: str = "", on_progress: ProgressCallback | None = None) -> str:
        """음성 파일의 글. 실패하면 `TranscribeError`. 진행(0~1)은 `on_progress` 로 알린다."""
        raise NotImplementedError

    def model_name(self) -> str:
        """받아쓴 글을 다시 써도 되는지 가를 모델 식별 글 (모델이 바뀌면 저장해 둔 글을 쓰지 않는다)."""
        return ""

    def label(self) -> str:
        """점검 화면에 보일 상태 문구."""
        return "사용 중" if self.available() else "사용 안 함"

    def prepare_model(self, say: Callable[[str], None] = print) -> None:
        raise TranscribeError("이 엔진은 모델 준비가 필요 없습니다.")

    def self_test(self) -> str:
        raise TranscribeError("이 엔진은 자체 점검을 지원하지 않습니다.")


class NoneTranscriber(Transcriber):
    def available(self) -> bool:
        return False

    def transcribe(self, path: Path, *, hints: str = "", on_progress: ProgressCallback | None = None) -> str:
        raise TranscribeError("음성 받아쓰기를 사용하지 않도록 설정되어 있습니다.")


# ---- 글 만들기 ----

def _clock(seconds: float) -> str:
    s = int(seconds)
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def format_segments(segments: Iterable[tuple[float, float, str]]) -> str:
    """(시작, 끝, 글) 목록을 한 조각에 한 줄로, 약 1분마다 줄 앞에 `[분:초]` 표시를 붙인 글로. 화자 표시는 없다."""
    lines: list[str] = []
    next_marker = 0.0
    for start, _end, text in segments:
        text = " ".join(str(text).split())
        if not text:
            continue
        if start >= next_marker:
            lines.append(f"[{_clock(start)}] {text}")
            next_marker = (int(start) // MARKER_EVERY_S + 1) * MARKER_EVERY_S
        else:
            lines.append(text)
    return "\n".join(lines)


# ---- faster-whisper ----

def _package_installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _cached_model_dir(name: str, root: Path) -> Path | None:
    """이름으로 고른 모델이 `root` 에 이미 받아져 있으면 그 폴더, 아니면 None. 인터넷을 쓰지 않는다."""
    try:
        from faster_whisper.utils import download_model
        return Path(download_model(name, local_files_only=True, cache_dir=str(root)))
    except Exception:  # noqa: BLE001 - 없으면 어떤 오류든 "받아져 있지 않음"
        return None


_READY: set[tuple[str, str]] = set()
_MODELS: dict[tuple, object] = {}
_MODELS_LOCK = threading.Lock()


def _is_named(model: str) -> bool:
    return model in MODEL_NAMES


class FasterWhisperTranscriber(Transcriber):
    """CPU(int8)로 서버 안에서 받아쓴다. 음성은 서버 밖으로 나가지 않는다."""

    def __init__(self, settings: "Settings"):
        self.settings = settings
        self.last_audio_seconds = None

    @classmethod
    def from_settings(cls, settings: "Settings") -> "FasterWhisperTranscriber":
        return cls(settings)

    # 상태
    def model_name(self) -> str:
        return self.settings.transcribe_model

    def model_label(self) -> str:
        m = self.settings.transcribe_model
        return m if _is_named(m) else Path(m).name

    def _models_dir(self) -> Path:
        return self.settings.models_dir

    def problem(self) -> str:
        if not _package_installed():
            return INSTALL_HINT
        m = self.settings.transcribe_model
        key = (m, str(self._models_dir()))
        if key in _READY:
            return ""
        if _is_named(m):
            ok = _cached_model_dir(m, self._models_dir()) is not None
        else:
            ok = (Path(m) / "model.bin").is_file()
        if not ok:
            return f"모델 '{self.model_label()}' 이(가) 아직 없습니다. 서버 PC에서 {SETUP_HINT} 를 실행해 받으세요."
        _READY.add(key)
        return ""

    def available(self) -> bool:
        return not self.problem()

    def label(self) -> str:
        return self.problem() or f"faster-whisper, {self.model_label()}, CPU"

    # 모델
    def _import(self):
        os.environ.setdefault("HF_HOME", str(self._models_dir() / ".hf_home"))  # 사용자 프로필에 캐시를 만들지 않는다
        import faster_whisper
        return faster_whisper

    def _load_model(self):
        fw = self._import()
        m = self.settings.transcribe_model
        return fw.WhisperModel(
            m, device="cpu", compute_type="int8", cpu_threads=self.settings.transcribe_threads,
            download_root=str(self._models_dir()), local_files_only=True,
        )

    def _model(self):
        key = (self.settings.transcribe_model, str(self._models_dir()), self.settings.transcribe_threads)
        with _MODELS_LOCK:
            if key not in _MODELS:
                try:
                    _MODELS[key] = self._load_model()
                except Exception:  # noqa: BLE001
                    log.exception("받아쓰기 모델을 불러오지 못했습니다")
                    raise TranscribeError(
                        f"받아쓰기 모델을 불러오지 못했습니다. {SETUP_HINT} 를 다시 실행해 보거나 관리자에게 알려 주세요."
                    ) from None
            return _MODELS[key]

    def transcribe(self, path: Path, *, hints: str = "", on_progress: ProgressCallback | None = None) -> str:
        problem = self.problem()
        if problem:
            raise TranscribeError(problem)
        model = self._model()
        lang = self.settings.transcribe_language
        try:
            segments, info = model.transcribe(
                str(path), language=None if lang == "auto" else lang, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=hints or None,
            )
            duration = float(getattr(info, "duration", 0) or 0)
            self.last_audio_seconds = duration or None
            limit = self.settings.transcribe_max_minutes
            if duration > limit * 60:
                raise TranscribeError(f"음성이 너무 깁니다 (최대 {limit}분, 이 파일은 약 {int(duration // 60)}분). 나눠서 올려 주세요.")
            done: list[tuple[float, float, str]] = []
            for seg in segments:
                done.append((float(seg.start), float(seg.end), seg.text))
                if on_progress and duration > 0:
                    on_progress(min(1.0, max(0.0, float(seg.end) / duration)))
            return format_segments(done)
        except (TranscribeError, TranscribeCancelled):
            raise
        except Exception:  # noqa: BLE001 - 라이브러리 오류의 자세한 내용은 기록에만 남긴다
            log.exception("음성 받아쓰기 오류 (%s)", Path(path).name)
            raise TranscribeError(
                "음성을 받아쓰지 못했습니다. 파일이 손상되었거나 지원하지 않는 형식일 수 있습니다."
            ) from None

    # 운영 명령
    def prepare_model(self, say: Callable[[str], None] = print) -> None:
        """설정한 모델을 `data_dir/models/` 에 받는다. 서버가 인터넷에 접속하는 유일한 곳이다."""
        if not _package_installed():
            raise TranscribeError(INSTALL_HINT)
        m = self.settings.transcribe_model
        if not _is_named(m):
            if not (Path(m) / "model.bin").is_file():
                raise TranscribeError(f"모델 폴더에 model.bin 이 없습니다: {m}")
            say(f"로컬 모델 폴더를 쓰므로 받을 것이 없습니다: {m}")
            return
        self._models_dir().mkdir(parents=True, exist_ok=True)
        self._import()
        from faster_whisper.utils import download_model
        say(f"모델 '{m}' 을(를) {self._models_dir()} 에 받습니다 (처음 한 번만, 크기에 따라 수 분 걸립니다)…")
        try:
            download_model(m, cache_dir=str(self._models_dir()))
        except Exception as e:  # noqa: BLE001
            log.exception("모델 내려받기 실패")
            raise TranscribeError(
                "모델을 내려받지 못했습니다. 인터넷 연결(모델 저장소 접속)과 디스크 여유 공간을 확인하세요. "
                f"({type(e).__name__})"
            ) from None
        _READY.discard((m, str(self._models_dir())))

    def self_test(self) -> str:
        """무음 1초를 받아써 모델이 읽히고 도는지만 확인한다 (무음은 글이 없는 것이 정상)."""
        import tempfile
        self._models_dir().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._models_dir()) as tmp:
            wav = Path(tmp) / "silence.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 16000)
            text = self.transcribe(wav)
        return "자체 점검 통과 (무음 1초: " + ("글이 없는 것이 정상입니다)" if not text else f"받아쓴 글 {text!r})")


TRANSCRIBERS: dict[str, type[Transcriber]] = {"none": NoneTranscriber, "faster-whisper": FasterWhisperTranscriber}


def get(settings: "Settings") -> Transcriber:
    return TRANSCRIBERS[settings.transcriber].from_settings(settings)


def label(settings: "Settings") -> str:
    return get(settings).label()


def run(transcriber: Transcriber, path: Path, *, hints: str = "", on_progress: ProgressCallback | None = None) -> str:
    """받아쓰기를 프로세스 전체에서 한 번에 하나만 돌린다 (워커가 여럿이어도). 옛 방식(`transcribe(path)`)의 엔진도 받는다."""
    params = inspect.signature(transcriber.transcribe).parameters
    takes_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())
    kwargs = {k: v for k, v in (("hints", hints), ("on_progress", on_progress)) if takes_kw or k in params}
    with _RUN_LOCK:
        return transcriber.transcribe(path, **kwargs)
