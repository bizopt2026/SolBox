"""명함 파일 가져오기의 정리안 만들기 (베타 AA). AI 를 쓰지 않고 파서가 읽은 카드(`cardfile.Card`)로 올리기 정리안(`Intake.plan`)을 직접 만든다.

정리안의 항목 종류는 AI 정리와 같다 (`add_contact`·`new_customer`·`new_partner`·`unplaced`) 라 확인·저장 화면과 `intake.apply` 가 그대로 쓰인다.
다른 점은 항목마다 `from_cards: True` 가 붙는 것뿐이다. 회사 이름은 `normalize_name(…, company=True)` 가 정확히 같을 때만 등록된 고객·파트너로 본다
(부분 일치로 추측하지 않는다). 커밋하지 않는다."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import aliases, cardfile, intake as il, ontology
from app.ai.tasks import normalize_name
from app.models import Customer, Intake, Partner

SOURCE = "cards"
MAX_UNPLACED_LINES = 500
KIND_CHOICES = ("customer", "partner")
SAME_AS_PARTNER = "같은 이름의 파트너에도 있어요. 파트너에 넣으려면 이 항목의 \"이 항목 제외\"를 풀고 고객 쪽을 제외하세요."


def is_cards(intake: Intake) -> bool:
    return getattr(intake, "source", "") == SOURCE


def _flat(text: str) -> str:
    return il._flat(text)


def contact_values(card: cardfile.Card) -> dict:
    """카드를 `add_contact` 칸으로: 직책 칸에는 부서와 직함을, 전화 칸에는 휴대폰·유선(`/`로 이음)을, 팩스·집 전화는 메모로."""
    note = " · ".join(x for x in (card.note, "팩스 " + " / ".join(card.faxes) if card.faxes else "") if x)
    values = {
        "person": card.person, "role": " ".join(x for x in (card.department, card.role) if x),
        "phone": " / ".join([*card.mobiles, *card.phones]), "email": ", ".join(card.emails),
        "address": card.address, "note": note,
    }
    return {f: _flat(values[f])[:il.CONTACT_CAPS[f]].strip() for f in il.CONTACT_FIELDS}


def usable(values: dict) -> bool:
    return any(values[f] for f in il.USABLE_CONTACT_FIELDS)


def _keys(card: cardfile.Card) -> set[str]:
    """같은 사람인지 가르는 열쇠: 휴대폰 숫자와 이메일 (사무실 번호는 동료와 같을 수 있어 쓰지 않는다)."""
    keys = {"m" + il._phone_key(n) for n in card.mobiles if len(il._phone_key(n)) >= 5}
    keys |= {"e" + e.lower() for e in card.emails if e}
    return keys


def _merge(into: cardfile.Card, other: cardfile.Card) -> None:
    for f in ("person", "department", "role", "address"):
        if not getattr(into, f) and getattr(other, f):
            setattr(into, f, getattr(other, f))
    for f in ("mobiles", "phones", "faxes", "emails"):
        have = getattr(into, f)
        have.extend(x for x in getattr(other, f) if x not in have)
    if other.note and other.note not in into.note:
        into.note = " · ".join(x for x in (into.note, other.note) if x)


def _where(parsed: cardfile.Parsed, rows: list[int | None]) -> str:
    unit = "카드" if parsed.format == "vcf" else "줄"
    shown = [str(r) for r in rows if r is not None]
    text = ", ".join(shown[:6]) + (" 외" if len(shown) > 6 else "")
    return f"{unit} {text}" if text else ""


def _line(card: cardfile.Card, where: str) -> str:
    who = " ".join(x for x in (card.person, card.department, card.role) if x)
    parts = [who, " / ".join([*card.mobiles, *card.phones]), ", ".join(card.emails)]
    text = " · ".join(p for p in parts if p) or "(연락처 정보 없음)"
    return f"{text} ({where})" if where else text


def _dup_note(target, v: dict, card: cardfile.Card) -> str:
    """같은 사람이 이미 있으면 그 까닭 글. 사람 이름이 있는 카드는 담당자 기록과 견주고 (영업 D: 저장하면 담당자를 만든다),
    이름이 없는 카드는 연락처 글에 같은 휴대폰·이메일(없으면 같은 줄)이 있는지 본다."""
    from app import contacts as ct

    if (v.get("person") or "").strip():
        same = ct.same_person(target.contacts, v["person"], v["phone"], v["email"])
        return f"같은 담당자({same.name})가 이미 있어요" if same is not None else ""
    existing = target.contact
    if not (existing or "").strip():
        return ""
    if any(il._phone_key(n) in il._phone_keys(existing) for n in card.mobiles if len(il._phone_key(n)) >= 5):
        return "같은 휴대폰 번호가 이미 연락처에 있어요"
    if any(e.lower() in il._email_keys(existing) for e in card.emails):
        return "같은 이메일이 이미 연락처에 있어요"
    if il._already_listed(existing, v):
        return "같은 연락처가 이미 적혀 있어요"
    return ""


def build_plan(db: Session, intake: Intake, parsed: cardfile.Parsed, default_kind: str = "customer", today: date | None = None) -> dict:
    today = today or date.today()
    default_kind = default_kind if default_kind in KIND_CHOICES else "customer"
    number = il.file_number(intake, intake.files[0]) if intake.files else 1
    customers = aliases.lookup(db, Customer, company=True)  # 이름과 다른 이름 (베타 AE)
    partners = aliases.lookup(db, Partner, company=True)
    customers.pop("", None)
    partners.pop("", None)

    groups: dict[str, dict] = {}
    unplaced: list[str] = []
    merged_away = 0
    for card in parsed.cards:
        where = _where(parsed, [card.row])
        key = normalize_name(card.company, company=True)
        if not key:
            unplaced.append(_line(card, where) + " — 회사 이름이 없어요")
            continue
        group = groups.setdefault(key, {"name": _flat(card.company), "cards": [], "rows": []})
        keys = _keys(card)
        same = next((m for m in group["cards"] if (keys and keys & m["keys"])
                     or (not keys and not m["keys"] and card.person and normalize_name(card.person) == normalize_name(m["card"].person))), None)
        if same is not None:
            _merge(same["card"], card)
            same["keys"] |= keys
            same["rows"].append(card.row)
            merged_away += 1
        else:
            group["cards"].append({"card": card, "keys": keys, "rows": [card.row]})

    new_partners: list[dict] = []
    new_customers: list[dict] = []
    contacts: list[dict] = []
    base = {"files": [number], "from_cards": True}

    def contact_item(target_type: str, target, entry: dict, skip_note: str = "", extra_skip: bool = False) -> dict:
        card, values = entry["card"], contact_values(entry["card"])
        where = _where(parsed, entry["rows"])
        dup = _dup_note(target, values, card)
        item = {"kind": "add_contact", "reason": f"명함 파일 {where}" + (" · 등록된 " + ("파트너" if target_type == "partner" else "고객") + "와 회사 이름이 같음"),
                **base, "target_type": target_type, "target_id": target.id, "target_name": target.name, **values}
        note = dup + (" — 제외로 해 두었어요. 그래도 넣으려면 \"이 항목 제외\"를 풀어 주세요." if dup else "")
        if extra_skip:
            note = skip_note
        if dup or extra_skip:
            item["skip_default"], item["skip_note"] = True, note
        return item

    for key, group in groups.items():
        entries = [e for e in group["cards"] if usable(contact_values(e["card"]))]
        cust, part = customers.get(key), partners.get(key)
        if cust or part:
            for e in group["cards"]:  # 등록된 회사인데 담당자 정보가 없으면 덧붙일 것이 없다 (새 회사는 회사만 만든다)
                if e not in entries:
                    unplaced.append(_line(e["card"], _where(parsed, e["rows"])) + " — 연락처 정보가 없어요")
            for e in entries:
                if cust:
                    contacts.append(contact_item("customer", cust, e))
                if part:
                    contacts.append(contact_item("partner", part, e, SAME_AS_PARTNER if cust else "", bool(cust)))
            continue
        lines = [il.contact_line(contact_values(e["card"]), intake.files, today) for e in entries]
        rows = [r for e in group["cards"] for r in e["rows"]]
        kind = "new_partner" if default_kind == "partner" else "new_customer"
        item = {"kind": kind, "reason": f"명함 파일 {_where(parsed, rows)} (등록된 고객·파트너에 없는 회사)", **base,
                "name": group["name"], "industry": "", "description": "", "memo": "",
                "contact": "\n".join(x for x in lines if x), "card_count": len(entries)}
        (new_partners if kind == "new_partner" else new_customers).append(item)

    items: list[dict] = []
    for prefix, group in (("p", new_partners), ("c", new_customers), ("ct", contacts)):
        for i, item in enumerate(group, 1):
            items.append({"kind": item["kind"], "key": f"{prefix}{i}", **{k: v for k, v in item.items() if k != "kind"}})
    if unplaced:
        shown = unplaced[:MAX_UNPLACED_LINES]
        if len(unplaced) > len(shown):
            shown.append(f"… 외 {len(unplaced) - len(shown)}장")
        items.append({"kind": ontology.INTAKE_ITEM_UNPLACED, "key": "u1", "reason": "회사 이름이나 연락처 정보가 없어 어디에 넣을지 알 수 없는 명함",
                      "files": [number], "from_cards": True, "lines": shown, "line_count": len(unplaced)})
    return {
        "source": SOURCE, "format": parsed.format, "cards": parsed.row_count, "merged": merged_away, "default_kind": default_kind,
        "header_row": parsed.header_row + 1, "mapping": [{"field": m["field"], "label": m["label"], "column": m["column"], "index": parsed.mapping[m["field"]]} for m in parsed.mapping_view()],
        "ignored": [h for i, h in enumerate(parsed.headers) if h and i not in parsed.mapping.values()][:20],
        "items": items,
    }
