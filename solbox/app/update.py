"""시스템 업데이트 (관리 › 시스템): GitHub main 의 새 버전을 확인하고(읽기만) 적용한다(백업 → git pull --ff-only → 필요하면 pip → 서버 다시 시작).

- 명령은 모두 `app.system.run_bounded_ex`(시간 제한, Windows 는 taskkill)로 **고정 인자 목록**만 돌린다: 셸 글을 쓰지 않고, 사용자가 입력한 값은 명령에 들어가지 않는다.
  (저장소 경로는 코드 위치에서, 커밋 번호는 git 출력에서 와서 40자리 16진수인지 확인한 것만 쓴다.)
- 마지막 확인 결과는 **메모리**(`_LAST_CHECK`)에만 둔다: 서버를 다시 시작하면 사라지고 "업데이트 확인"을 다시 누르면 된다.
- 마지막 업데이트 기록은 `app_settings` 의 `system_update_last` 한 줄이다 (누가·언제·이전→새 커밋·pip 실행 여부·결과·한 번만 보일 알림 표시).
- 적용은 백그라운드 스레드 하나(`_APPLY_LOCK`)이고 화면은 `/admin/system/update/status` 로 진행을 본다.
- 서버를 다시 시작하는 일은 `scripts/run_server.ps1` 이 한다 (이 프로세스가 끝나면 10초 뒤 다시 띄운다). 그래서 그 스크립트가 켠 서버(`SOLBOX_SUPERVISED=1`)에서만 적용한다."""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from app import backup, ontology
from app.models import AIJob, AppSetting
from app.system import run_bounded_ex

log = logging.getLogger("app")

SOLBOX_DIR = Path(__file__).resolve().parent.parent  # `pip install -e` 로 설치하는 폴더 (pyproject.toml 이 있는 곳)
RECORD_KEY = "system_update_last"
SUPERVISED_ENV = "SOLBOX_SUPERVISED"
BRANCH = "main"
REMOTE = "origin"

T_QUICK = 15  # 읽기만 하는 git 명령의 제한(초)
T_STATUS = 30
T_FETCH = 60
T_PULL = 120
T_PIP = 15 * 60
RESTART_DELAY_S = 3.0  # 응답을 보내고 진행 화면이 "마쳤다" 를 읽을 시간을 준 뒤 끝낸다
HARD_EXIT_S = 20.0  # 얌전한 종료가 이 시간 안에 끝나지 않으면 강제로 끝낸다 (run_server.ps1 이 다시 띄운다)
MAX_FILES_SHOWN = 10
MAX_COMMITS_SHOWN = 30
ERR_MAX = 300

NOT_GIT_TEXT = "git 으로 설치한 폴더가 아니라 업데이트 버튼을 쓸 수 없어요. 운영가이드 9번의 zip 방법을 쓰세요."
NOT_SUPERVISED_TEXT = "서버를 run_server.ps1 로 시작하지 않아서 업데이트 뒤 다시 시작할 수 없어요. 운영가이드의 '자동 시작'대로 run_server.ps1 로 서버를 시작한 뒤 쓰세요."
_SHA = re.compile(r"[0-9a-f]{40}")


class GitError(Exception):
    """git·pip 명령을 돌리지 못했거나 실패했다. 글은 화면에 그대로 보여도 되는 짧은 한국어 설명."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # 로그인 질문으로 멈추지 않는다 (공개 저장소라 필요 없다)
    env["GCM_INTERACTIVE"] = "never"
    return env


def _short_err(text: str) -> str:
    from app.ai.runner import redact

    text = " ".join(redact(text or "").split())
    return text[:ERR_MAX] + ("…" if len(text) > ERR_MAX else "")


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        return run_bounded_ex(cmd, timeout, _env())
    except subprocess.TimeoutExpired:
        raise GitError(f"{timeout:g}초 안에 끝나지 않아 멈췄어요.") from None
    except FileNotFoundError:
        raise GitError(f"'{cmd[0]}' 을 찾을 수 없어요. 설치되어 있고 PATH 에 있는지 확인하세요.") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise GitError(f"실행하지 못했어요 ({_short_err(str(e))}).") from None


def _git(root: Path, args: list[str], timeout: float, keep_lead: bool = False) -> str:
    """git 명령 한 번. 성공하면 표준 출력(끝 공백 뺀; `keep_lead` 면 줄 머리 공백은 둔다: porcelain 상태 줄)을, 실패하면 `GitError`(짧은 오류 글)."""
    code, out, err = _run(["git", "-C", str(root), "-c", "core.quotepath=off", *args], timeout)
    if code != 0:
        raise GitError(_short_err(err or out) or f"종료 코드 {code}")
    return out.rstrip() if keep_lead else out.strip()


def find_repo_root(start: Path | None = None) -> Path | None:
    """앱 폴더에서 위로 올라가며 `.git` 이 있는 첫 폴더. 앱 폴더에 pyproject.toml 이 없으면(다른 곳에 설치된 앱) 없음."""
    start = (start or SOLBOX_DIR).resolve()
    if not (start / "pyproject.toml").is_file():
        return None
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    return None


def is_supervised() -> bool:
    return os.environ.get(SUPERVISED_ENV) == "1"


def _pyproject_path(root: Path) -> str:
    rel = SOLBOX_DIR.resolve().relative_to(root).as_posix()
    return "pyproject.toml" if rel in ("", ".") else f"{rel}/pyproject.toml"


def pyproject_changed(root: Path, old: str, new: str) -> bool:
    names = _git(root, ["diff", "--name-only", old, new], T_QUICK).splitlines()
    return _pyproject_path(root) in [n.strip() for n in names]


def active_ai_jobs(session_factory) -> int:
    with session_factory() as db:
        return db.query(AIJob).filter(AIJob.status.in_(ontology.JOB_ACTIVE)).count()


# ---------- 시작할 때의 커밋 (돌고 있는 코드의 버전) ----------

_BOOT: dict = {}


def remember_boot_commit() -> None:
    """서버가 시작될 때 한 번: 지금 돌고 있는 코드의 커밋. (pull 로 폴더의 HEAD 가 바뀌어도 이 값은 다시 시작할 때까지 그대로다.) git 이 아니면 빈 값."""
    if _BOOT:
        return
    info = {"sha": "", "subject": ""}
    root = find_repo_root()
    if root is not None:
        try:
            line = _git(root, ["log", "-1", "--format=%H%n%s"], T_QUICK).splitlines()
            if line and _SHA.fullmatch(line[0]):
                info = {"sha": line[0], "subject": line[1] if len(line) > 1 else ""}
        except GitError:
            pass
    _BOOT.update(info)


def boot_commit() -> dict:
    remember_boot_commit()
    return dict(_BOOT)



# ---------- 실행 중에 폴더의 코드가 바뀌었는지 (베타 Z) ----------

_DISK_CACHE: dict = {"at": 0.0, "sha": ""}
_DISK_TTL = 30.0


def _read_head_sha(root: Path) -> str:
    """git 을 실행하지 않고 .git 파일만 읽어 지금 폴더의 HEAD 커밋을 돌려준다. 모르면 빈 값."""
    git = root / ".git"
    if git.is_file():  # 작업 트리(worktree): "gitdir: <경로>"
        text = git.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return ""
        git = (root / text.split(":", 1)[1].strip()).resolve()
    head = (git / "HEAD").read_text(encoding="utf-8").strip()
    if _SHA.fullmatch(head):
        return head
    if not head.startswith("ref:"):
        return ""
    ref = head.split(":", 1)[1].strip()
    common = git
    if (git / "commondir").is_file():
        common = (git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve()
    for base in (git, common):
        f = base / ref
        if f.is_file():
            sha = f.read_text(encoding="utf-8").strip()
            return sha if _SHA.fullmatch(sha) else ""
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _SHA.fullmatch(parts[0]):
                return parts[0]
    return ""


def code_changed_on_disk() -> dict | None:
    """서버가 시작한 뒤 폴더의 코드(HEAD)가 바뀌었으면 {"running", "disk"} (짧은 번호), 아니면 None.
    git pull 만 하고 서버를 다시 시작하지 않은 상태를 관리자에게 알리는 데 쓴다. 30초마다 한 번만 파일을 읽고, 어떤 오류도 None 이다."""
    try:
        running = (_BOOT or {}).get("sha", "")
        if not running:
            return None
        now = time.monotonic()
        if now - _DISK_CACHE["at"] > _DISK_TTL:
            root = find_repo_root()
            _DISK_CACHE["sha"] = _read_head_sha(root) if root is not None else ""
            _DISK_CACHE["at"] = now
        disk = _DISK_CACHE["sha"]
        if disk and disk != running:
            return {"running": running[:7], "disk": disk[:7]}
    except Exception:  # noqa: BLE001 - 알림일 뿐이다. 화면을 깨뜨리지 않는다
        return None
    return None

# ---------- 확인 (읽기만) ----------

_LAST_CHECK: dict | None = None
_CHECK_LOCK = threading.Lock()
_APPLY_LOCK = threading.Lock()


def last_check() -> dict | None:
    return _LAST_CHECK


def check(session_factory) -> dict:
    """업데이트를 확인한다 (읽기 전용: fetch 가 `origin/main` 기록만 갱신한다). 결과 dict:
    status: no_git | fetch_failed | error | up_to_date | available | blocked(막는 이유가 있다)
    blockers: 막는 이유 글 목록, can_apply: 적용해도 되는가, commits: [(짧은 번호, 제목)], behind: 새 커밋 수 …"""
    res: dict = {"at": datetime.now(), "status": "error", "blockers": [], "can_apply": False, "commits": [], "behind": 0, "pyproject": False,
                 "dirty": [], "dirty_more": 0, "ai_active": 0, "message": "", "branch": "", "head": "", "head_short": "", "head_subject": "", "remote_sha": ""}
    root = find_repo_root()
    if root is None:
        res.update(status="no_git", message=NOT_GIT_TEXT)
        return res
    blockers: list[str] = res["blockers"]
    try:
        res["branch"] = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"], T_QUICK)
        if res["branch"] != BRANCH:
            blockers.append(f"지금 '{res['branch']}' 브랜치예요. '{BRANCH}' 브랜치에서만 업데이트할 수 있어요.")
        head = _git(root, ["rev-parse", "HEAD"], T_QUICK)
        if not _SHA.fullmatch(head):
            raise GitError("현재 커밋을 읽지 못했어요.")
        res["head"], res["head_short"] = head, head[:7]
        res["head_subject"] = _git(root, ["log", "-1", "--format=%s"], T_QUICK)
        dirty = [ln for ln in _git(root, ["status", "--porcelain", "--untracked-files=no"], T_STATUS, keep_lead=True).splitlines() if ln.strip()]
        if dirty:
            res["dirty"] = [ln[3:].strip() if len(ln) > 3 else ln.strip() for ln in dirty[:MAX_FILES_SHOWN]]
            res["dirty_more"] = max(len(dirty) - MAX_FILES_SHOWN, 0)
            blockers.append("서버 폴더에서 직접 고친 파일이 있어요. 업데이트하면 덮어쓸 수 있어서 막았어요.")
    except GitError as e:
        res.update(status="error", message=f"git 으로 현재 상태를 읽지 못했어요: {e}")
        return res
    if not is_supervised():
        blockers.append(NOT_SUPERVISED_TEXT)
    try:
        res["ai_active"] = active_ai_jobs(session_factory)
    except Exception:  # noqa: BLE001 - DB 를 못 읽어도 확인 화면은 보여 준다 (적용은 막는다)
        log.exception("업데이트 확인: AI 작업 수를 읽지 못했습니다")
        blockers.append("AI 작업 수를 읽지 못해 막았어요.")
    if res["ai_active"]:
        blockers.append(f"AI 작업이 {res['ai_active']}건 진행·대기 중이에요. 끝난 뒤에 하세요 (다시 시작하면 진행 중인 작업이 끊겨요).")
    try:
        _git(root, ["fetch", REMOTE, BRANCH], T_FETCH)
    except GitError as e:
        res.update(status="fetch_failed", message=f"GitHub 에 연결할 수 없어요: {e}")
        return res
    try:
        remote = _git(root, ["rev-parse", f"{REMOTE}/{BRANCH}"], T_QUICK)
        res["remote_sha"] = remote
        res["behind"] = int(_git(root, ["rev-list", "--count", f"HEAD..{REMOTE}/{BRANCH}"], T_QUICK) or "0")
        ahead = int(_git(root, ["rev-list", "--count", f"{REMOTE}/{BRANCH}..HEAD"], T_QUICK) or "0")
        if ahead:
            blockers.append(f"이 폴더에만 있는 커밋이 {ahead}개 있어요 (GitHub 에 없는 것). 그대로 이어 붙일 수 없어서 막았어요.")
        if res["behind"]:
            log_out = _git(root, ["log", "--format=%h %s", f"HEAD..{REMOTE}/{BRANCH}", "-n", str(MAX_COMMITS_SHOWN)], T_QUICK)
            res["commits"] = [tuple(ln.split(" ", 1)) if " " in ln else (ln, "") for ln in log_out.splitlines() if ln.strip()]
            res["pyproject"] = pyproject_changed(root, "HEAD", f"{REMOTE}/{BRANCH}")
    except (GitError, ValueError) as e:
        res.update(status="error", message=f"새 버전 정보를 읽지 못했어요: {e}")
        return res
    if not res["behind"]:
        res.update(status="blocked" if blockers else "up_to_date", message="이미 최신 버전이에요.")
        return res
    res["can_apply"] = not blockers
    res.update(status="blocked" if blockers else "available", message=f"새 커밋 {res['behind']}개가 있어요.")
    return res


def run_check(session_factory) -> dict:
    """확인을 돌리고 결과를 메모리에 둔다. 적용 중이거나 다른 확인이 도는 중이면 그 사실만 돌려준다."""
    global _LAST_CHECK
    if _APPLY_LOCK.locked():
        return {**(_LAST_CHECK or {}), "busy": "업데이트를 적용하는 중이라 지금은 확인하지 않아요."}
    if not _CHECK_LOCK.acquire(blocking=False):
        return {**(_LAST_CHECK or {}), "busy": "다른 확인이 진행 중이에요. 잠시 뒤에 다시 눌러 주세요."}
    try:
        _LAST_CHECK = check(session_factory)
        return _LAST_CHECK
    finally:
        _CHECK_LOCK.release()


# ---------- 적용 ----------

_STATE_LOCK = threading.Lock()
_STATE: dict = {"state": "idle", "steps": [], "error": "", "to": "", "from": "", "pip": False}


def state() -> dict:
    with _STATE_LOCK:
        return {**_STATE, "steps": list(_STATE["steps"])}


def _step(text: str, status: str = "run") -> None:
    with _STATE_LOCK:
        steps = _STATE["steps"]
        if steps and steps[-1][1] == "run":
            steps[-1] = (steps[-1][0], "ok")
        steps.append((text, status))


def _set(**kw) -> None:
    with _STATE_LOCK:
        _STATE.update(kw)


def run_pip() -> None:
    """`<이 파이썬> -m pip install -e <솔박스 폴더>` (시험에서는 이 함수를 바꿔 끼운다)."""
    code, out, err = _run([sys.executable, "-m", "pip", "install", "-e", str(SOLBOX_DIR)], T_PIP)
    if code != 0:
        raise GitError("pip 설치가 실패했어요: " + (_short_err(err or out) or f"종료 코드 {code}"))


def _write_record(session_factory, record: dict) -> None:
    try:
        with session_factory() as db:
            row = db.get(AppSetting, RECORD_KEY)
            if row is None:
                db.add(AppSetting(key=RECORD_KEY, value=record))
            else:
                row.value = record
            db.commit()
    except Exception:  # noqa: BLE001 - 기록을 못 남겨도 결과는 화면에 보인다
        log.exception("업데이트 기록을 저장하지 못했습니다")


def read_record(session_factory) -> dict | None:
    with session_factory() as db:
        row = db.get(AppSetting, RECORD_KEY)
        return dict(row.value) if row is not None and isinstance(row.value, dict) else None


def take_notice(session_factory) -> dict | None:
    """한 번만 보이는 결과 알림: 기록에 `notice_pending` 이 있으면 돌려주고 끈다. 성공은 새 코드로 다시 시작한 서버에서만 (그 전에는 남겨 둔다)."""
    with session_factory() as db:
        row = db.get(AppSetting, RECORD_KEY)
        if row is None or not isinstance(row.value, dict) or not row.value.get("notice_pending"):
            return None
        rec = dict(row.value)
        if rec.get("ok") and rec.get("to") and rec["to"] != boot_commit()["sha"]:
            return None  # 아직 옛 코드가 돌고 있다 (다시 시작 중)
        row.value = {**rec, "notice_pending": False}
        db.commit()
        return rec


def _finish(session_factory, record: dict, who: str) -> None:
    record["at"] = datetime.now().isoformat(timespec="seconds")
    record["by"] = who
    record["notice_pending"] = True
    _write_record(session_factory, record)
    log.info("시스템 업데이트 %s: %s → %s, pip=%s, %s%s", "성공" if record["ok"] else "실패", record.get("from", "")[:7], record.get("to", "")[:7],
             record.get("pip"), who, "" if record["ok"] else f", {record.get('error')}")


def _apply(app, who: str) -> None:
    sf = app.state.session_factory
    root = find_repo_root()
    record: dict = {"ok": False, "from": "", "to": "", "pip": False, "reset": False, "error": "", "commits": 0}
    old = ""
    try:
        _step("업데이트할 수 있는지 다시 확인하는 중")
        res = check(sf)  # 이전 화면의 확인은 믿지 않는다
        if not res["can_apply"]:
            reason = " ".join(res["blockers"]) or res["message"] or "업데이트할 수 없는 상태예요."
            raise GitError(reason)
        assert root is not None
        old = res["head"]
        record.update({"from": old, "commits": res["behind"]})
        _set(**{"from": old})
        _step("백업을 만드는 중")
        try:
            backup.run_backup(app.state.settings)
        except (OSError, ValueError, Exception) as e:  # noqa: BLE001 - 백업이 안 되면 멈춘다
            raise GitError(f"백업하지 못해서 업데이트하지 않았어요: {_short_err(str(e))}") from None
        _step("새 버전을 받는 중 (git pull)")
        moved = False
        try:
            _git(root, ["pull", "--ff-only", REMOTE, BRANCH], T_PULL)
        except GitError:
            try:
                moved = _git(root, ["rev-parse", "HEAD"], T_QUICK) != old
            except GitError:
                moved = True  # 모르면 되돌리는 쪽 (아래 reset 은 old 로만 간다)
            if moved:
                _rollback(root, old, record)
            raise
        new = _git(root, ["rev-parse", "HEAD"], T_QUICK)
        if not _SHA.fullmatch(new):
            _rollback(root, old, record)
            raise GitError("새 커밋을 읽지 못했어요.")
        record["to"] = new
        _set(to=new)
        try:
            need_pip = pyproject_changed(root, old, new)
            if need_pip:
                _step("필요한 패키지를 설치하는 중 (pip install -e, 몇 분 걸릴 수 있어요)")
                record["pip"] = True
                _set(pip=True)
                run_pip()
        except GitError:
            _rollback(root, old, record)
            raise
        record["ok"] = True
        _step("서버를 다시 시작할 준비를 하는 중")
    except GitError as e:
        record["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - 알 수 없는 오류도 화면에 남긴다
        log.exception("시스템 업데이트 중 오류")
        record["error"] = f"알 수 없는 오류: {_short_err(str(e))}"
        if root is not None and old:
            try:
                if _git(root, ["rev-parse", "HEAD"], T_QUICK) != old:
                    _rollback(root, old, record)
            except GitError:
                pass
    if record["ok"]:
        _finish(sf, record, who)
        _set(state="done", error="")
        schedule_restart()
    else:
        _finish(sf, record, who)
        _set(state="failed", error=record["error"])
        with _STATE_LOCK:
            if _STATE["steps"]:
                _STATE["steps"][-1] = (_STATE["steps"][-1][0], "fail")


def _rollback(root: Path, old: str, record: dict) -> None:
    """pull 이 HEAD 를 옮긴 뒤 실패했을 때만 부른다: 옛 커밋으로 되돌린다 (결과는 record 에 적는다)."""
    if not _SHA.fullmatch(old):
        record["error"] += " (이전 커밋을 몰라 되돌리지 못했어요)"
        return
    _step("실패해서 이전 버전으로 되돌리는 중")
    try:
        _git(root, ["reset", "--hard", old], T_PULL)
        record["reset"] = True
    except GitError as e:
        record["reset"] = False
        record["error"] = (record.get("error") or "") + f" 이전 버전으로 되돌리지도 못했어요 ({e}). 운영가이드를 보고 직접 확인하세요."


def _worker(app, who: str) -> None:
    try:
        _apply(app, who)
    except Exception:  # noqa: BLE001
        log.exception("시스템 업데이트 스레드가 멈췄습니다")
        _set(state="failed", error="알 수 없는 오류로 멈췄어요. 로그를 확인하세요.")
    finally:
        _APPLY_LOCK.release()


def start_apply(app, who: str) -> bool:
    """적용을 백그라운드에서 시작한다. 이미 돌고 있으면 False (두 번 눌러도 한 번만 돈다)."""
    if not _APPLY_LOCK.acquire(blocking=False):
        return False
    with _STATE_LOCK:
        _STATE.update({"state": "running", "steps": [], "error": "", "to": "", "from": "", "pip": False})
    try:
        threading.Thread(target=_worker, args=(app, who), name="system-update", daemon=True).start()
    except BaseException:
        _APPLY_LOCK.release()
        raise
    return True


# ---------- 다시 시작 ----------

def request_shutdown() -> None:
    """uvicorn 의 얌전한 종료를 부른다 (SIGINT: 새 요청을 끊고, 진행 중 요청을 마치고, `lifespan` 의 정리(작업자·스케줄러·로그 닫기)를 한 뒤 끝난다)."""
    signal.raise_signal(signal.SIGINT)


def hard_exit() -> None:
    """얌전한 종료가 시간 안에 끝나지 않을 때만: 로그를 비우고 바로 끝낸다."""
    logging.shutdown()
    os._exit(0)


def _restart_worker(delay: float, hard_after: float) -> None:
    time.sleep(delay)
    log.info("시스템 업데이트: 서버를 다시 시작하려고 종료합니다")
    try:
        request_shutdown()
    except Exception:  # noqa: BLE001
        log.exception("얌전한 종료를 부르지 못했습니다")
    time.sleep(hard_after)
    log.warning("시스템 업데이트: %g초 안에 끝나지 않아 강제로 끝냅니다", hard_after)
    hard_exit()


def schedule_restart(delay: float | None = None, hard_after: float | None = None) -> None:
    threading.Thread(target=_restart_worker, args=(RESTART_DELAY_S if delay is None else delay, HARD_EXIT_S if hard_after is None else hard_after),
                     name="system-restart", daemon=True).start()
