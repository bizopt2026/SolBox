"""중복 후보 찾기 (베타 AE, 관리 › 중복 정리): 같은 회사가 둘로 등록된 고객·파트너 쌍을 찾아 보여 준다. 아무것도 자동으로 합치지 않는다.

기계적 신호(AI 없이, 쌍마다 맞은 신호를 보인다):
- `name`   이름·다른 이름의 정규형이 같다 (`normalize_name(company=True)`: ㈜·(주)·주식회사·공백·기호·대소문자 무시)
- `hangul` 영문 약자를 한글 글자 이름으로 바꾼 이름이 같다 ("HS홀딩스" ≈ "에이치에스홀딩스", "LG전자" ≈ "엘지전자", "KT" ≈ "케이티")
- `inside` 한쪽 정규형이 다른 쪽에 들어 있다 (짧은 쪽이 3자 이상: `match_mentions` 와 같은 기준)
- `email`  연락처 글에 같은 이메일 도메인이 있다 (무료 메일 도메인 `FREE_MAIL_DOMAINS` 은 무시)
AI 신호(`ai`)는 이름 목록만 보내 받은 쌍이다 (`ai/duplicate_tasks.py`): 결과는 작업 결과(`AIJob.result`)에 있고 여기서 읽어 걸러 보인다.
"다른 회사임" 으로 표시한 쌍(`DuplicateDismissal`)은 어느 신호든 보이지 않는다."""
from __future__ import annotations

import itertools
import json
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import aliases, merge
from app.ai.tasks import normalize_name
from app.models import AIJob, DuplicateDismissal

JOB_KIND = "duplicate_names"
MIN_INSIDE = 3  # 포함 비교에 쓰는 이름의 최소 길이 (정규형 기준, match_mentions 와 같다)
MAX_PAIRS = 200  # 화면에 보이는 쌍의 상한
MAX_Z_VARIANTS = 3  # Z 는 "제트"·"지" 두 가지로 읽는다: 한 이름 안에서 Z 가 이만큼 넘으면 첫 읽기만 쓴다

# 영문 알파벳의 한글 글자 이름 (한 곳). Z 는 "제트" 와 "지" 둘 다 쓴다.
LETTER_NAMES = {
    "a": "에이", "b": "비", "c": "씨", "d": "디", "e": "이", "f": "에프", "g": "지", "h": "에이치", "i": "아이", "j": "제이",
    "k": "케이", "l": "엘", "m": "엠", "n": "엔", "o": "오", "p": "피", "q": "큐", "r": "알", "s": "에스", "t": "티",
    "u": "유", "v": "브이", "w": "더블유", "x": "엑스", "y": "와이", "z": "제트",
}
Z_ALTERNATE = "지"

# 같은 도메인이어도 같은 회사라는 뜻이 아닌 무료 메일 (한 곳)
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com",
})
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", re.IGNORECASE)

SIGNALS = {"name": "이름 같음", "hangul": "영문 약자 ↔ 한글 표기", "inside": "이름이 서로 포함", "email": "같은 이메일 도메인", "ai": "AI"}


def hangul_forms(name: str) -> set[str]:
    """이름의 영문자 덩어리를 한글 글자 이름으로 바꾼 정규형들 (영문자가 없으면 정규형 하나). Z 가 있으면 "제트"·"지" 두 읽기."""
    key = normalize_name(name, True)
    if not key:
        return set()
    parts = re.split(r"([a-z]+)", key)
    options: list[list[str]] = []
    zs = 0
    for part in parts:
        if part and part[0] in LETTER_NAMES and part.isascii():
            letters = []
            for ch in part:
                if ch == "z":
                    zs += 1
                    letters.append([LETTER_NAMES["z"], Z_ALTERNATE] if zs <= MAX_Z_VARIANTS else [LETTER_NAMES["z"]])
                else:
                    letters.append([LETTER_NAMES[ch]])
            options.extend(letters)
        else:
            options.append([part])
    return {"".join(c) for c in itertools.product(*options)}


def email_domains(text: str | None) -> set[str]:
    return {d.lower() for d in _EMAIL.findall(text or "")} - FREE_MAIL_DOMAINS


def pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def dismissed(db: Session, kind: str) -> set[tuple[int, int]]:
    return {(r.low_id, r.high_id) for r in db.scalars(select(DuplicateDismissal).where(DuplicateDismissal.kind == kind))}


def dismiss(db: Session, kind: str, a: int, b: int, user_id: int | None) -> bool:
    """"다른 회사임" 표시 (같은 쌍을 두 번 눌러도 한 줄). 두 기록이 모두 있을 때만. 새로 적었으면 True."""
    model = merge.KINDS[kind]
    low, high = pair_key(a, b)
    if low == high or db.get(model, low) is None or db.get(model, high) is None:
        return False
    if db.get(DuplicateDismissal, (kind, low, high)) is not None:
        return False
    db.add(DuplicateDismissal(kind=kind, low_id=low, high_id=high, dismissed_by_id=user_id))
    return True


def undismiss(db: Session, kind: str, a: int, b: int) -> None:
    low, high = pair_key(a, b)
    db.execute(delete(DuplicateDismissal).where(
        DuplicateDismissal.kind == kind, DuplicateDismissal.low_id == low, DuplicateDismissal.high_id == high))


def mechanical_pairs(db: Session, kind: str) -> dict[tuple[int, int], list[dict]]:
    """신호가 하나라도 맞은 쌍 -> 맞은 신호들 [{"code", "detail"}] (표시 제외 전)."""
    rows = list(db.scalars(select(merge.KINDS[kind]).order_by(merge.KINDS[kind].id)))
    info = {}
    for o in rows:
        names = [o.name or "", *aliases.of(o)]
        info[o.id] = {
            "keys": aliases.keys_of(o, True),
            "hangul": set().union(*(hangul_forms(n) for n in names)) - {""},
            "domains": email_domains(o.contact),
        }
    found: dict[tuple[int, int], dict[str, str]] = {}

    def hit(a: int, b: int, code: str, detail: str = "") -> None:
        found.setdefault(pair_key(a, b), {}).setdefault(code, detail)

    ids = [o.id for o in rows]
    by_key: dict[str, list[int]] = {}
    by_hangul: dict[str, list[int]] = {}
    by_domain: dict[str, list[int]] = {}
    for i in ids:
        for k in info[i]["keys"]:
            by_key.setdefault(k, []).append(i)
        for k in info[i]["hangul"]:
            by_hangul.setdefault(k, []).append(i)
        for d in info[i]["domains"]:
            by_domain.setdefault(d, []).append(i)
    for group in by_key.values():
        for a, b in itertools.combinations(group, 2):
            hit(a, b, "name")
    for key, group in by_hangul.items():
        for a, b in itertools.combinations(group, 2):
            if not (info[a]["keys"] & info[b]["keys"]):  # 정규형이 같으면 이미 "이름 같음"
                hit(a, b, "hangul", key)
    for domain, group in by_domain.items():
        for a, b in itertools.combinations(group, 2):
            hit(a, b, "email", domain)
    long_keys = {i: sorted((k for k in info[i]["keys"] if len(k) >= MIN_INSIDE), key=len) for i in ids}
    for a, b in itertools.combinations(ids, 2):
        for ka in long_keys[a]:
            if any(ka != kb and (ka in kb or kb in ka) for kb in long_keys[b]):
                hit(a, b, "inside")
                break
    return {p: [{"code": c, "detail": d} for c, d in sig.items()] for p, sig in found.items()}


def ai_pairs(db: Session, kind: str) -> tuple[dict[tuple[int, int], str], AIJob | None, AIJob | None]:
    """가장 최근에 끝난 AI 확인의 쌍 {(작은 번호, 큰 번호): 이유}와 (그 작업, 진행 중이거나 마지막으로 실패한 작업).
    결과에 든 번호가 지금 없으면 건너뛴다."""
    jobs = [j for j in db.scalars(select(AIJob).where(AIJob.kind == JOB_KIND).order_by(AIJob.id.desc()).limit(30))
            if (j.input or {}).get("type") == kind]
    latest = jobs[0] if jobs else None
    done = next((j for j in jobs if j.status == "done"), None)
    pairs: dict[tuple[int, int], str] = {}
    if done is not None:
        try:
            items = json.loads(done.result or "{}").get("pairs", [])
        except (ValueError, AttributeError):
            items = []
        existing = set(db.scalars(select(merge.KINDS[kind].id)))
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and isinstance(it.get("a"), int) and isinstance(it.get("b"), int):
                a, b = it["a"], it["b"]
                if a != b and a in existing and b in existing:
                    pairs.setdefault(pair_key(a, b), str(it.get("reason", "")).strip())
    status_job = latest if latest is not None and (latest.status != "done") else None
    return pairs, done, status_job


def candidates(db: Session, kind: str) -> tuple[list[dict], list[dict]]:
    """(후보 줄들, "다른 회사임" 으로 표시한 쌍 줄들). 후보 줄 = {"a", "b", "signals": [{"code","label","detail"}], "links_a", "links_b"}."""
    model = merge.KINDS[kind]
    hidden = dismissed(db, kind)
    found = mechanical_pairs(db, kind)
    ai, _done, _job = ai_pairs(db, kind)
    for pair, reason in ai.items():
        found.setdefault(pair, [])
        found[pair] = [*found[pair], {"code": "ai", "detail": reason}]
    objs = {o.id: o for o in db.scalars(select(model))}
    rows = []
    for (a, b), signals in found.items():
        if (a, b) in hidden or a not in objs or b not in objs:
            continue
        rows.append({"a": objs[a], "b": objs[b], "signals": [{**s, "label": SIGNALS[s["code"]]} for s in signals],
                     "links_a": merge.total_links(db, kind, objs[a]), "links_b": merge.total_links(db, kind, objs[b])})
    rows.sort(key=lambda r: (-len(r["signals"]), "ai" in {s["code"] for s in r["signals"]}, r["a"].id, r["b"].id))
    hidden_rows = [{"a": objs[a], "b": objs[b]} for a, b in sorted(hidden) if a in objs and b in objs]
    return rows[:MAX_PAIRS], hidden_rows
