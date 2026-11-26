"""담당자(사람) 규칙 한 곳 (영업 D 단계).
- 같은 사람 판단 `find_same`: 같은 회사 안에서 휴대폰 숫자가 같거나 이메일(소문자)이 같으면 같은 사람.
  새 사람에 휴대폰·이메일이 둘 다 없으면 이름(공백 무시)이 같은 담당자를 같은 사람으로 본다.
- 연락처 글 나누기 파서 `parse_line`·`parse_text`: AI 를 쓰지 않는다 (개인 정보). 이메일·전화는 정규식,
  끝의 `(…)` 출처 꼬리는 메모, 남은 글의 첫 낱말이 한글 2~4자 또는 영문 이름이면 이름, 나머지는 직함.
- `split_rows`: 회사의 연락처 글을 후보 줄로 (이미 있는 담당자·이름 없는 줄은 처음부터 "넣지 않음")."""
from __future__ import annotations

import re

from app import ontology

FIELDS = ("name", "title", "role", "phone", "office_phone", "email", "note")

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+82[-.\s]?|82[-.\s])?\(?0?\d{1,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")
TAIL_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
HANGUL_NAME_RE = re.compile(r"^[가-힣]{2,4}$")
ENGLISH_WORD_RE = re.compile(r"^[A-Z][a-z]+(?:[-'][A-Za-z]+)?$")
# 사람 이름이 아닌 흔한 첫 낱말 (대표번호·본사 같은 줄을 사람으로 잡지 않는다)
NOT_NAMES = {"대표", "대표번호", "본사", "지사", "사무실", "사무소", "팩스", "고객센터", "전화", "휴대폰", "핸드폰", "연락처", "담당", "담당자",
             "주소", "메일", "이메일", "문의", "영업", "영업팀", "구매", "구매팀", "기술", "기술팀", "총무", "인사", "회사", "공장", "연구소", "주식회사"}
NOT_NAME_ENDS = ("팀", "부", "실", "센터", "본부", "번호", "사무소", "지점", "공장", "연구소", "그룹")
ENGLISH_TITLES = {"Manager", "Director", "President", "Engineer", "Sales", "Team", "Lead", "Leader", "Head", "Senior", "Junior", "Chief", "Officer",
                  "Vice", "General", "Executive", "Partner", "Owner", "Founder", "Consultant", "Specialist", "Staff", "Assistant", "Office", "Main"}


def digits(text: str | None) -> str:
    """전화 글의 숫자만 (+82 10… 은 010… 으로)."""
    d = re.sub(r"\D", "", text or "")
    if d.startswith("82") and len(d) >= 11:
        d = "0" + d[2:]
    return d


def _name_key(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def is_mobile(text: str) -> bool:
    return digits(text).startswith("01")


def find_same(contacts, name: str = "", phone: str = "", email: str = "", exclude=None):
    """같은 회사의 담당자들 `contacts` 가운데 같은 사람(맨 처음 것). 없으면 None."""
    p, e, n = digits(phone), (email or "").strip().lower(), _name_key(name)
    for c in contacts:
        if exclude is not None and c is exclude:
            continue
        if p and digits(c.phone) == p:
            return c
        if e and (c.email or "").strip().lower() == e:
            return c
        if not p and not e and n and _name_key(c.name) == n:
            return c
    return None


def _is_name(word: str) -> bool:
    if HANGUL_NAME_RE.match(word):
        return word not in NOT_NAMES and not word.endswith(NOT_NAME_ENDS)
    return bool(ENGLISH_WORD_RE.match(word)) and word not in ENGLISH_TITLES


def parse_line(line: str) -> dict:
    """연락처 글 한 줄 → 담당자 칸 `{name, title, role, phone, office_phone, email, note}` (role 은 늘 빈 값).
    이름을 못 찾으면 name 이 빈 글."""
    text = (line or "").strip()
    notes: list[str] = []
    if m := TAIL_RE.search(text):  # 끝의 (명함, 2026-11-26 올리기) 같은 출처 꼬리
        notes.append(m.group(1).strip())
        text = text[: m.start()]
    emails = EMAIL_RE.findall(text)
    text = EMAIL_RE.sub(" ", text)
    phones = [p.strip() for p in PHONE_RE.findall(text)]
    text = PHONE_RE.sub(" ", text)
    mobiles = [p for p in phones if is_mobile(p)]
    others = [p for p in phones if not is_mobile(p)]
    chunks = [c.strip(" ,/|:;-\t") for c in re.split(r"\s+·\s+|·|\||/|,|;", text)]
    chunks = [re.sub(r"\s+", " ", c) for c in chunks if c.strip(" ,/|:;-\t")]
    name = title = ""
    if chunks:
        words = chunks[0].split(" ")
        if words and _is_name(words[0]):
            take = 1
            if not HANGUL_NAME_RE.match(words[0]) and len(words) > 1 and _is_name(words[1]):
                take = 2  # 영문 이름은 두 낱말까지 (John Smith)
            name = " ".join(words[:take])
            title = " ".join(words[take:])
            rest = chunks[1:]
        else:
            rest = chunks
        notes = rest + notes
    return {
        "name": name, "title": title, "role": "",
        "phone": mobiles[0] if mobiles else "",
        "office_phone": ", ".join(others + mobiles[1:]),
        "email": ", ".join(emails),
        "note": " · ".join(n for n in notes if n),
    }


def parse_text(text: str | None) -> list[dict]:
    """여러 줄 연락처 글 → 줄마다 `parse_line` 결과 (빈 줄은 건너뛴다). 각 결과에 원래 줄 `line` 을 붙인다."""
    out = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        if raw.strip():
            out.append({**parse_line(raw), "line": raw.strip()})
    return out


def first_email(text: str) -> str:
    return (text or "").split(",")[0].strip()


def split_rows(company) -> list[dict]:
    """나누기 화면의 후보 줄: 파서 결과 + `existing`(같은 사람 담당자 또는 None) + `checked`(처음부터 넣기)."""
    rows = []
    for cand in parse_text(company.contact):
        existing = find_same(company.contacts, cand["name"], cand["phone"], first_email(cand["email"])) if cand["name"] else None
        rows.append({**cand, "existing": existing, "checked": bool(cand["name"]) and existing is None})
    return rows


def pending_split_count(company) -> int:
    """연락처 글 가운데 이름이 잡히고 아직 담당자 기록에 없는 줄 수 (상세의 나누기 링크)."""
    return sum(1 for r in split_rows(company) if r["checked"])


def clean(values: dict) -> dict:
    """폼 값 정리: 앞뒤 공백, 역할은 코드 목록에 있을 때만."""
    out = {f: (values.get(f) or "").replace("\r\n", "\n").strip() for f in FIELDS}
    if out["role"] not in ontology.CONTACT_ROLES:
        out["role"] = ""
    return out


def role_label(code: str) -> str:
    return ontology.CONTACT_ROLES.get(code or "", "")


def _mobiles_and_others(phone_text: str) -> tuple[list[str], list[str]]:
    found = [p.strip() for p in PHONE_RE.findall(phone_text or "")]
    if not found and digits(phone_text):
        found = [(phone_text or "").strip()]
    return [p for p in found if is_mobile(p)], [p for p in found if not is_mobile(p)]


def same_person(contacts, name: str, phone_text: str = "", email_text: str = ""):
    """`find_same` 를 여러 번호·이메일이 든 글로: 휴대폰 하나라도 같거나 이메일 하나라도 같으면, 둘 다 없으면 이름이 같으면 같은 사람."""
    mobiles, _ = _mobiles_and_others(phone_text)
    emails = [e.lower() for e in EMAIL_RE.findall(email_text or "")]
    for m in mobiles:
        if (c := find_same(contacts, name, m, "")) is not None:
            return c
    for e in emails:
        if (c := find_same(contacts, name, "", e)) is not None:
            return c
    if not mobiles and not emails:
        return find_same(contacts, name)
    return None


def from_intake(v: dict) -> dict:
    """올리기·명함의 연락처 칸(person·role·phone·email·address·note) → 담당자 칸. 휴대폰은 첫 휴대폰, 나머지 번호는 사무실 전화,
    주소는 메모에 "주소: …" 로 붙인다 (DB 칸을 늘리지 않는다)."""
    mobiles, others = _mobiles_and_others(v.get("phone", ""))
    note = " · ".join(x for x in ((v.get("note") or "").strip(), ("주소: " + v["address"].strip()) if (v.get("address") or "").strip() else "") if x)
    return {"name": (v.get("person") or "").strip(), "title": (v.get("role") or "").strip(), "role": "",
            "phone": mobiles[0] if mobiles else "", "office_phone": ", ".join(others + mobiles[1:]),
            "email": ", ".join(EMAIL_RE.findall(v.get("email", ""))) or (v.get("email") or "").strip(), "note": note}


def split_for_new_company(text: str) -> tuple[list[dict], str]:
    """새 회사의 연락처 글 → (이름이 잡힌 줄의 담당자 칸들, 이름이 없는 줄만 남긴 연락처 글)."""
    people, rest = [], []
    for row in parse_text(text):
        if row["name"]:
            people.append({f: row[f] for f in FIELDS})
        else:
            rest.append(row["line"])
    return people, "\n".join(rest)
