"""명함 앱 내보내기 파일 읽기 (베타 AA): `.vcf`(vCard 2.1/3.0/4.0), `.csv`(UTF-8·BOM·CP949), `.xlsx`(첫 시트). AI 를 쓰지 않는다.

`parse(이름, 바이트, 열 지정=None)` 이 `Parsed` 를 돌려준다. csv·xlsx 는 첫 5줄 안에서 머리글 줄을 찾고, 머리글의 한국어·영어 낱말(`SYNONYMS`)로
열을 정한다. 이름이나 회사 열을 찾지 못하면 `needs_mapping` 이 참이고 카드는 비어 있다 (사용자가 열을 직접 지정한 뒤 다시 부른다: 추측하지 않는다).
다른 파일 모양(.xls, 이미지, 구글·아웃룩 연락처의 성/이름 분리 등)은 지원하지 않는다.
"""
from __future__ import annotations

import csv
import io
import quopri
import re
import unicodedata
import warnings
from dataclasses import dataclass, field

MAX_ROWS = 2000  # 한 파일의 명함 수 상한 (넘으면 오류)
HEADER_SCAN_ROWS = 5  # 머리글 줄을 찾는 줄 수
MAX_COLUMNS = 60
MAX_SCAN_ROWS = 20000  # 빈 줄이 많은 시트에서 읽는 줄 수 상한
EXTENSIONS = (".vcf", ".csv", ".xlsx")

# 칸 이름 -> 화면 이름 (순서가 열 지정 화면의 순서). 이름·회사는 꼭 필요하다.
FIELDS = {
    "person": "이름", "company": "회사", "department": "부서", "role": "직함·직책", "mobile": "휴대폰", "phone": "전화(유선)",
    "fax": "팩스", "email": "이메일", "address": "주소", "note": "메모",
}
REQUIRED = ("person", "company")

# 머리글 낱말(공백·문장부호 없이 소문자). 괄호 안 글("이름(한글)"), 끝 숫자("이메일2")를 뗀 꼴도 견준다. 정확히 같을 때만 맞는다 (부분 일치로 추측하지 않는다).
# 홈페이지·등록일 같은 낱말은 일부러 넣지 않았다: 어느 칸에도 맞지 않는 열은 무시한다.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "person": ("이름", "성명", "성함", "이름한글", "담당자", "담당자명", "name", "fullname", "contactname", "displayname"),
    "company": ("회사", "회사명", "회사이름", "소속", "기관", "기관명", "업체", "업체명", "기업", "기업명", "직장", "직장명", "조직",
                "company", "companyname", "organization", "organisation", "org", "employer"),
    "department": ("부서", "부서명", "소속부서", "팀", "department", "dept", "division"),
    "role": ("직함", "직책", "직위", "직급", "직무", "title", "jobtitle", "position"),
    "mobile": ("휴대폰", "휴대전화", "휴대폰번호", "휴대전화번호", "휴대", "모바일", "핸드폰", "핸드폰번호", "mobile", "mobilephone", "cell", "cellphone", "cellular"),
    "phone": ("전화", "전화번호", "유선", "유선전화", "유선전화번호", "사무실", "사무실전화", "회사전화", "회사전화번호", "직장전화", "대표전화", "근무처전화", "근무처전화번호",
              "tel", "telephone", "phone", "work", "workphone", "businessphone", "office", "officephone"),
    "fax": ("팩스", "팩스번호", "회사팩스", "근무처팩스", "팩시밀리", "fax", "faxnumber", "workfax"),
    "email": ("이메일", "이메일주소", "전자메일", "전자메일주소", "메일", "메일주소", "email", "emailaddress", "mail"),
    "address": ("주소", "회사주소", "사무실주소", "직장주소", "근무지주소", "근무지주소번지", "근무처주소", "address", "workaddress", "businessaddress"),
    "note": ("메모", "비고", "노트", "note", "notes", "memo", "remark", "remarks", "comment", "comments"),
}
_LOOKUP = {word: key for key, words in SYNONYMS.items() for word in words}


class CardFileError(ValueError):
    """사용자에게 그대로 보일 한국어 오류."""


@dataclass
class Card:
    person: str = ""
    company: str = ""
    department: str = ""
    role: str = ""
    mobiles: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    faxes: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    address: str = ""
    note: str = ""
    row: int | None = None  # 파일 안의 위치 (csv·xlsx 는 줄 번호, vcf 는 몇 번째 카드)


@dataclass
class Parsed:
    format: str  # vcf / csv / xlsx
    cards: list[Card]
    headers: list[str] = field(default_factory=list)  # csv·xlsx 머리글 줄의 칸 이름
    header_row: int = 0  # 0 부터의 머리글 줄 위치 (vcf 는 0)
    mapping: dict[str, int] = field(default_factory=dict)  # 칸 이름 -> 열 위치 (0 부터)
    needs_mapping: bool = False
    row_count: int = 0

    def mapping_view(self) -> list[dict]:
        """화면에 보일 "어느 열이 무엇이 되었나": [{"field", "label", "column"}] (찾은 것만, FIELDS 순서). vcf 는 빈 목록."""
        return [{"field": f, "label": FIELDS[f], "column": self.headers[self.mapping[f]] if self.mapping[f] < len(self.headers) else ""}
                for f in FIELDS if f in self.mapping]


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩﻿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _fix_phone(text: str, mobile: bool) -> str:
    """엑셀이 앞의 0 을 떼어 버린 휴대폰 번호(10 자리, 1[016789] 로 시작)에 0 을 되돌린다."""
    if mobile and re.fullmatch(r"1[016789]\d{8}", text):
        return "0" + text
    return text


# ---------------------------------------------------------------- 머리글

def _norm_header(text: str) -> str:
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] in "LN")


def header_field(text: str) -> str | None:
    """머리글 한 칸이 가리키는 칸 이름. 괄호 안 글과 끝 숫자를 뗀 꼴까지 정확히 같을 때만 맞는다."""
    raw = unicodedata.normalize("NFKC", str(text or "")).casefold()
    candidates = [_norm_header(raw), _norm_header(re.split(r"[(\[]", raw, maxsplit=1)[0])]
    candidates += [re.sub(r"\d+$", "", c) for c in candidates]
    for c in candidates:
        if c and c in _LOOKUP:
            return _LOOKUP[c]
    return None


def detect_mapping(row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for i, cell in enumerate(row):
        f = header_field(cell)
        if f and f not in mapping:  # 같은 칸으로 읽히는 열이 여럿이면 앞의 것
            mapping[f] = i
    return mapping


def find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """첫 5줄 중 칸 이름이 가장 많이 맞는 줄 (같으면 앞). 하나도 못 맞추면 첫 비지 않은 줄을 머리글로 두고 빈 매핑."""
    best, best_map = -1, {}
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        m = detect_mapping(row)
        if len(m) > len(best_map):
            best, best_map = i, m
    if best >= 0 and (len(best_map) >= 2 or set(best_map) & set(REQUIRED)):
        return best, best_map
    first = next((i for i, r in enumerate(rows[:HEADER_SCAN_ROWS]) if any(r)), 0)
    return first, {}


# ---------------------------------------------------------------- 표 (csv·xlsx)

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CardFileError("글자를 읽지 못했어요. 파일을 UTF-8 이나 CP949(EUC-KR) 글자 부호로 저장해 다시 올려 주세요.")


def _trim(row: list[str]) -> list[str]:
    """줄 끝의 빈 칸을 뗀다 (엑셀은 열 수를 채워 준다)."""
    while row and not row[-1]:
        row.pop()
    return row


def read_csv(data: bytes) -> list[list[str]]:
    text = _decode(data)
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        pass
    rows: list[list[str]] = []
    try:
        for raw in csv.reader(io.StringIO(text), delimiter=delimiter):
            rows.append(_trim([_clean(c) for c in raw[:MAX_COLUMNS]]))
            if len(rows) > MAX_SCAN_ROWS:
                break
    except csv.Error as e:
        raise CardFileError(f"csv 파일을 읽지 못했어요 ({e}).") from e
    return rows


def read_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    rows: list[list[str]] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                sheet = wb.worksheets[0]
                for raw in sheet.iter_rows(values_only=True, max_col=MAX_COLUMNS):
                    rows.append(_trim([_clean(c) for c in raw]))
                    if len(rows) > MAX_SCAN_ROWS:
                        break
            finally:
                wb.close()
    except Exception as e:  # noqa: BLE001 - 깨진 파일·암호 파일 등 어떤 오류도 같은 안내
        raise CardFileError("엑셀 파일을 열지 못했어요. .xlsx 파일이 맞는지, 암호가 걸려 있지 않은지 확인해 주세요.") from e
    return rows


def cards_from_table(rows: list[list[str]], header_row: int, mapping: dict[str, int]) -> list[Card]:
    cards: list[Card] = []
    for offset, row in enumerate(rows[header_row + 1:], start=header_row + 2):
        if not any(row):
            continue

        def get(f: str) -> str:
            i = mapping.get(f)
            return row[i] if i is not None and i < len(row) else ""

        card = Card(person=get("person"), company=get("company"), department=get("department"), role=get("role"),
                    address=get("address"), note=get("note"), row=offset)
        if get("mobile"):
            card.mobiles.append(_fix_phone(get("mobile"), True))
        if get("phone"):
            card.phones.append(get("phone"))
        if get("fax"):
            card.faxes.append(get("fax"))
        if get("email"):
            card.emails.append(get("email"))
        cards.append(card)
        if len(cards) > MAX_ROWS:
            raise CardFileError(_too_many())
    return cards


# ---------------------------------------------------------------- vCard

def _unfold(text: str) -> list[str]:
    """줄 접기(다음 줄 첫 글자가 공백·탭)와 2.1 의 quoted-printable 이어 쓰기(줄 끝 "=")를 풀어 논리적인 줄로."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    i = 0
    while i < len(raw):
        cur = raw[i]
        i += 1
        while True:
            head = cur.split(":", 1)[0].upper()
            if "QUOTED-PRINTABLE" in head and cur.endswith("=") and i < len(raw):
                cur = cur[:-1] + raw[i]
                i += 1
            elif i < len(raw) and raw[i][:1] in (" ", "\t"):
                cur += raw[i][1:]
                i += 1
            else:
                break
        lines.append(cur)
    return lines


def _split_unescaped(value: str, sep: str) -> list[str]:
    parts, cur, esc = [], [], False
    for ch in value:
        if esc:
            cur.append("\\" + ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if esc:
        cur.append("\\")
    parts.append("".join(cur))
    return parts


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: " " if m.group(1) in "nN" else m.group(1), value)


def _params(parts: list[str]) -> tuple[set[str], dict[str, str]]:
    """매개변수: (종류 낱말 집합(대문자), {이름: 값}). 2.1 의 이름 없는 낱말(`CELL`)과 3.0·4.0 의 `TYPE=cell,voice` 를 같게 본다."""
    types: set[str] = set()
    named: dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, _, v = p.partition("=")
            k, v = k.strip().upper(), v.strip().strip('"')
            if k == "TYPE":
                types.update(x.strip().upper() for x in v.split(","))
            else:
                named[k] = v
        elif p.strip():
            types.add(p.strip().upper())
    return types, named


def _value(raw: str, named: dict[str, str]) -> str:
    """값을 글자로: quoted-printable 이면 풀고 CHARSET(기본 UTF-8)으로 읽는다."""
    if named.get("ENCODING", "").upper() in ("QUOTED-PRINTABLE", "Q"):
        data = quopri.decodestring(raw.encode("utf-8", "ignore"))
        try:
            return data.decode(named.get("CHARSET", "utf-8"))
        except (LookupError, UnicodeDecodeError):
            return data.decode("utf-8", "replace")
    return raw


def _tel(value: str) -> str:
    value = value.strip()
    return value[4:] if value.lower().startswith("tel:") else value


_HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")


def read_vcf(data: bytes) -> list[Card]:
    cards: list[Card] = []
    cur: dict | None = None
    for line in _unfold(_decode(data)):
        if not line.strip():
            continue
        if line.strip().upper() == "BEGIN:VCARD":
            cur = {"fn": "", "n": "", "org": ["", ""], "title": "", "tels": [], "emails": [], "adrs": [], "note": []}
            continue
        if line.strip().upper() == "END:VCARD":
            if cur is not None:
                cards.append(_vcard_to_card(cur, len(cards) + 1))
                if len(cards) > MAX_ROWS:
                    raise CardFileError(_too_many())
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        left, _, raw = line.partition(":")
        pieces = left.split(";")
        name = pieces[0].split(".")[-1].upper()  # 애플의 그룹 접두어 (item1.TEL)
        if name not in ("FN", "N", "ORG", "TITLE", "TEL", "EMAIL", "ADR", "NOTE"):
            continue
        types, named = _params(pieces[1:])
        raw = _value(raw, named)
        if name == "FN":
            cur["fn"] = _clean(_unescape(raw))
        elif name == "N":
            parts = [_clean(_unescape(x)) for x in _split_unescaped(raw, ";")]
            family, given = (parts + ["", ""])[:2]
            cur["n"] = (family + given) if _HANGUL.search(family + given) else " ".join(x for x in (given, family) if x)
        elif name == "ORG":
            parts = [_clean(_unescape(x)) for x in _split_unescaped(raw, ";")]
            cur["org"] = [parts[0] if parts else "", " ".join(x for x in parts[1:] if x)]
        elif name == "TITLE":
            cur["title"] = _clean(_unescape(raw))
        elif name == "TEL":
            cur["tels"].append((types, _clean(_tel(_unescape(raw)))))
        elif name == "EMAIL":
            cur["emails"].append(_clean(_unescape(raw)))
        elif name == "ADR":
            cur["adrs"].append((types, [_clean(_unescape(x)) for x in _split_unescaped(raw, ";")]))
        elif name == "NOTE":
            cur["note"].append(_clean(_unescape(raw)))
    return cards


def _adr_text(parts: list[str]) -> str:
    pobox, ext, street, locality, region, postcode, country = (parts + [""] * 7)[:7]
    text = " ".join(x for x in (country, region, locality, street, ext, pobox) if x)
    return f"{text} ({postcode})" if postcode and text else text or postcode


def _vcard_to_card(v: dict, number: int) -> Card:
    card = Card(person=v["fn"] or v["n"], company=v["org"][0], department=v["org"][1], role=v["title"], row=number)
    extra = ""
    for types, tel in v["tels"]:
        if not tel:
            continue
        if "FAX" in types:
            card.faxes.append(tel)
        elif types & {"CELL", "MOBILE"}:
            card.mobiles.append(tel)
        elif "HOME" in types:
            extra += (" · " if extra else "") + "집 " + tel
        else:
            card.phones.append(tel)
    card.emails = [e for e in v["emails"] if e]
    if v["adrs"]:
        pick = next((p for t, p in v["adrs"] if "WORK" in t), v["adrs"][0][1])
        card.address = _adr_text(pick)
    note = " ".join(x for x in v["note"] if x)
    card.note = " · ".join(x for x in (note, extra) if x)
    return card


# ---------------------------------------------------------------- 진입점

def _too_many() -> str:
    return f"명함이 {MAX_ROWS:,}장을 넘어요. 파일을 나눠서 올려 주세요."


def build_mapping(headers: list[str], posted: dict[str, str]) -> dict[str, int]:
    """열 지정 화면의 값 `{칸 이름: "열 번호"}` 을 `{칸 이름: 열 위치}` 로. 목록 밖·빈 값은 버린다."""
    out: dict[str, int] = {}
    for f in FIELDS:
        text = str(posted.get(f, "")).strip()
        if text.isascii() and text.isdecimal() and int(text) < len(headers):
            out[f] = int(text)
    return out


def read_rows(name: str, data: bytes) -> list[list[str]]:
    """csv·xlsx 의 줄들 (열 지정 화면의 미리보기용). vcf 나 다른 확장자는 빈 목록."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    return read_csv(data) if ext == "csv" else read_xlsx(data) if ext == "xlsx" else []


def parse(name: str, data: bytes, mapping: dict[str, int] | None = None, header_row: int | None = None) -> Parsed:
    """파일 하나를 읽는다. `mapping`(와 `header_row`)을 주면 열 지정을 그대로 쓰고, 없으면 머리글로 찾는다.
    오류는 `CardFileError`. csv·xlsx 에서 이름·회사 열을 못 찾으면 `needs_mapping` (카드 없음)."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == "vcf":
        cards = read_vcf(data)
        parsed = Parsed("vcf", cards, row_count=len(cards))
    elif ext in ("csv", "xlsx"):
        rows = read_csv(data) if ext == "csv" else read_xlsx(data)
        if not any(any(r) for r in rows):
            raise CardFileError("파일에 내용이 없어요.")
        if header_row is None or not 0 <= header_row < min(len(rows), HEADER_SCAN_ROWS):
            hdr, found = find_header(rows)
        else:
            hdr, found = header_row, detect_mapping(rows[header_row])
        use = found if mapping is None else mapping
        parsed = Parsed(ext, [], headers=rows[hdr] if hdr < len(rows) else [], header_row=hdr, mapping=use)
        if not all(f in use for f in REQUIRED):
            parsed.needs_mapping = True
            return parsed
        parsed.cards = cards_from_table(rows, hdr, use)
        parsed.row_count = len(parsed.cards)
    else:
        raise CardFileError("명함 파일은 .vcf, .csv, .xlsx 만 가져올 수 있어요.")
    if not parsed.cards:
        raise CardFileError("파일에서 명함을 찾지 못했어요.")
    return parsed


def parse_card_file(path) -> tuple[dict[str, str], list[Card], list[str]]:
    """경로의 파일 하나를 읽는다: (칸 이름 -> 읽은 열의 머리글, 카드 목록, 문제 글 목록). 화면 없이 부르기 위한 얇은 껍데기 (읽은 열을 확인하는 데 쓴다).
    열을 못 찾았거나 읽지 못하면 카드가 비고 문제 글이 적힌다. 문제 글에는 개인 값을 넣지 않는다 (건수뿐)."""
    from pathlib import Path

    p = Path(path)
    try:
        parsed = parse(p.name, p.read_bytes())
    except (CardFileError, OSError) as e:
        return {}, [], [str(e) if isinstance(e, CardFileError) else "파일을 읽지 못했어요."]
    mapping = {m["field"]: m["column"] for m in parsed.mapping_view()}
    problems: list[str] = []
    if parsed.needs_mapping:
        problems.append("이름이나 회사 열을 찾지 못했어요: " + ", ".join(FIELDS[f] for f in REQUIRED if f not in parsed.mapping))
    for f in REQUIRED:
        n = sum(1 for c in parsed.cards if not getattr(c, f))
        if n:
            problems.append(f"{FIELDS[f]}가 비어 있는 명함 {n}장")
    return mapping, parsed.cards, problems
