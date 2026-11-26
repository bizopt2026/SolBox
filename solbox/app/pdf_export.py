"""제안서 PPT → PDF (베타 AJ). 브라우저에서 바로 여는 미리보기이고, 고객에게 보내는 파일로도 쓴다.

변환 프로그램은 서버 PC 에 이미 있는 것을 쓴다 (패키지를 더하지 않는다):
- `powerpoint`: Windows 의 PowerPoint 를 PowerShell COM 으로 부른다 (`Presentations.Open` → `SaveAs(…, 32 = PDF)`).
- `libreoffice`: `soffice --headless --convert-to pdf`. 서버 데이터 폴더 안의 따로 둔 사용자 프로필로 돌린다 (열려 있는 LibreOffice 와 부딪히지 않게).
설정 `pdf_converter` = `auto`(기본: Windows 는 PowerPoint → LibreOffice 순서, 그 밖에는 LibreOffice) | `powerpoint` | `libreoffice` | `none`.
LibreOffice 경로는 설정 `soffice_path`, 없으면 PATH 와 흔한 설치 경로에서 찾는다.
PowerPoint 를 서비스 계정·화면 없는 세션에서 부르면 실패하거나 멈출 수 있다: 시간 제한(`PDF_TIMEOUT_S`)을 넘으면 프로세스를 죽이고 다음 방법으로 넘어간다.
Windows 에서 실제로 돌려 보지 못했다 (README 수동 확인)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

PDF_TIMEOUT_S = 180
CONVERTERS = ("auto", "powerpoint", "libreoffice", "none")
_WINDOWS_SOFFICE = (
    Path("C:/Program Files/LibreOffice/program/soffice.exe"),
    Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
)
# 경로는 환경 변수로 넘긴다 (따옴표·한글 경로를 명령 글에 끼워 넣지 않는다). 32 = ppSaveAsPDF
_PPT_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$app = New-Object -ComObject PowerPoint.Application; "
    "try { $pres = $app.Presentations.Open($env:SOLBOX_PPTX, $true, $false, $false); "
    "try { $pres.SaveAs($env:SOLBOX_PDF, 32) } finally { $pres.Close() } } "
    "finally { $app.Quit() }"
)


class PdfError(Exception):
    """변환하지 못했다. 글은 화면에 내지 않고 로그에만 남긴다."""


def pdf_path(pptx: Path) -> Path:
    """제안서 파일 옆의 PDF (같은 이름, 확장자만 다르다). 새 제안서를 만들면 이름이 바뀌므로 옛 PDF 는 자연히 맞지 않는다."""
    return Path(pptx).with_suffix(".pdf")


def find_soffice(settings: Settings) -> Path | None:
    if settings.soffice_path:
        path = Path(settings.soffice_path)
        return path if path.is_file() else None
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    if sys.platform == "win32":
        return next((p for p in _WINDOWS_SOFFICE if p.is_file()), None)
    return None


def methods(settings: Settings) -> list[str]:
    """설정과 이 PC 에서 쓸 수 있는 변환 방법 (시도할 순서). 빈 목록이면 PDF 를 만들 수 없다 (화면에 버튼을 내지 않는다)."""
    choice = settings.pdf_converter
    out: list[str] = []
    if choice in ("auto", "powerpoint") and sys.platform == "win32" and shutil.which("powershell"):
        out.append("powerpoint")
    if choice in ("auto", "libreoffice") and find_soffice(settings) is not None:
        out.append("libreoffice")
    return out


def describe(settings: Settings) -> str:
    """점검 화면의 한 줄."""
    found = methods(settings)
    if settings.pdf_converter == "none":
        return "사용 안 함"
    if not found:
        return "변환 프로그램이 없어 PDF 버튼이 보이지 않습니다 (PowerPoint 또는 LibreOffice 를 설치하면 쓸 수 있습니다)"
    names = {"powerpoint": "PowerPoint (실제로 열리는지는 PDF 를 만들 때 확인)", "libreoffice": f"LibreOffice ({find_soffice(settings)})"}
    return " → ".join(names[m] for m in found)


def _run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    from app.system import run_bounded_ex

    try:
        code, out, err = run_bounded_ex(cmd, PDF_TIMEOUT_S, env)
    except subprocess.TimeoutExpired as e:
        raise PdfError(f"{PDF_TIMEOUT_S}초 안에 끝나지 않았습니다") from e
    except OSError as e:
        raise PdfError(f"실행하지 못했습니다 ({e})") from e
    if code != 0:
        raise PdfError(f"종료 코드 {code}: {(err or out).strip()[:300]}")


def _powerpoint(pptx: Path, pdf: Path) -> None:
    env = {**os.environ, "SOLBOX_PPTX": str(pptx.resolve()), "SOLBOX_PDF": str(pdf.resolve())}
    _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PPT_SCRIPT], env)


def _libreoffice(pptx: Path, pdf: Path, settings: Settings, work: Path) -> None:
    soffice = find_soffice(settings)
    if soffice is None:
        raise PdfError("LibreOffice 를 찾지 못했습니다")
    profile = (work / "lo-profile").resolve()
    out_dir = work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([str(soffice), f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--convert-to", "pdf",
          "--outdir", str(out_dir.resolve()), str(pptx.resolve())])
    made = out_dir / pdf_path(Path(pptx.name)).name
    if not made.is_file():
        raise PdfError("PDF 파일이 생기지 않았습니다")
    os.replace(made, pdf)


def convert(pptx: Path, settings: Settings) -> Path:
    """`pptx` 옆에 PDF 를 만들어 그 경로를 돌려준다. 방법을 차례로 시도하고, 모두 실패하면 `PdfError`.
    작업 폴더는 데이터 폴더(`decks/.pdf-work/<무작위>`) 안이고 끝나면 지운다. PDF 는 임시 이름에 쓴 뒤 바꿔치기한다."""
    pptx = Path(pptx)
    found = methods(settings)
    if not found:
        raise PdfError("쓸 수 있는 변환 프로그램이 없습니다")
    target = pdf_path(pptx)
    work = settings.decks_dir / ".pdf-work" / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    temp = work / "deck.pdf"
    errors = []
    try:
        for method in found:
            try:
                if method == "powerpoint":
                    _powerpoint(pptx, temp)
                else:
                    _libreoffice(pptx, temp, settings, work)
                if not temp.is_file() or temp.stat().st_size == 0:
                    raise PdfError("PDF 파일이 비어 있습니다")
                from app.deck import replace_retry

                replace_retry(temp, target)
                return target
            except PdfError as e:
                log.warning("PDF 변환 실패 (%s): %s", method, e)
                errors.append(f"{method}: {e}")
        raise PdfError("; ".join(errors))
    finally:
        shutil.rmtree(work, ignore_errors=True)
