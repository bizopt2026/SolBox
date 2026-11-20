"""DB 항목의 Markdown 사본을 knowledge 폴더에 쓴다 (Claude CLI 가 읽는 용도)."""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app import checks, labels, ontology
from app.extract import extract_text
from app.ontology import mirror_folder, parse_rel, prop as P, rel_path  # noqa: F401  (경로 형식은 ontology 가 정의한다)
from app.models import Activity, AppSetting, Customer, Partner, Project, ResearchReport, Solution, SolutionFile

log = logging.getLogger(__name__)
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def safe_name(name: str) -> str:
    """파일 이름에 쓸 수 없는 글자를 `_` 로 바꾸고 80자로 자른다."""
    cleaned = _UNSAFE.sub("_", name).strip(" .")[:80].strip(" .")
    return cleaned or "file"


def file_markdown(f: SolutionFile, text: str) -> str:
    """자료 파일에서 뽑은 본문을 사본 형식으로 만든다."""
    return f"# {f.original_name} ({labels.FILE_KINDS.get(f.kind, f.kind)})\n\n{text}\n"


def _item(label: str, value) -> str | None:
    text = "" if value is None else str(value).strip()
    return f"- {label}: " + text.replace("\n", "\n  ") if text else None


def _inline(text) -> str:
    """웹에서 온 글을 한 줄로 만든다: 줄바꿈·탭은 공백 하나로, 링크를 깨는 대괄호는 뺀다."""
    return re.sub(r"\s+", " ", str(text or "")).replace("[", "").replace("]", "").strip()


def _quote(text: str) -> list[str]:
    """여러 줄 글을 인용문으로 만든다. 웹에서 온 글이 제목 줄로 시작할 수 없다."""
    return [f"> {line}".rstrip() for line in str(text).strip().splitlines()]


def _lines(*items: str | None) -> list[str]:
    return [i for i in items if i]


def _section(title: str, body: str) -> list[str]:
    body = (body or "").strip()
    return ["", f"## {title}", "", body] if body else []


class KnowledgeMirror:
    def __init__(self, root: Path):
        self.root = Path(root)

    # ----- 경로 -----
    def _rel(self, obj) -> str:
        if isinstance(obj, Activity):
            return rel_path("activity", year=obj.date.year if obj.date else "undated", id=obj.id)
        if isinstance(obj, SolutionFile):
            return rel_path("solution_file", solution_id=obj.solution_id, id=obj.id, name=safe_name(obj.original_name))
        if isinstance(obj, ResearchReport):
            day = (obj.created_at or datetime.now()).date().isoformat()
            return rel_path("research_report", day=day, tag=obj.area if obj.kind == "area" else "suggest", id=obj.id)
        try:
            return rel_path(ontology.type_of(obj), id=obj.id)
        except (TypeError, KeyError):
            raise TypeError(f"지원하지 않는 항목: {type(obj).__name__}") from None

    def rel_path(self, obj) -> str:
        return self._rel(obj)

    def path_for(self, obj) -> Path:
        return self.root / self._rel(obj)

    def _ref(self, name: str, obj) -> str:
        return f"{name} ({self._rel(obj)})"

    # ----- 쓰기·지우기 -----
    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def sync(self, obj) -> None:
        if isinstance(obj, Activity):
            for old in (self.root / mirror_folder("activity")).glob(f"*/{obj.id}.md"):
                old.unlink(missing_ok=True)
        render = {
            Solution: self._solution,
            Customer: self._customer,
            Project: self._project,
            Partner: self._partner,
            Activity: self._activity,
            ResearchReport: self._report,
        }.get(type(obj))
        if render is None:
            raise TypeError(f"지원하지 않는 항목: {type(obj).__name__}")
        self._write(self.path_for(obj), "\n".join(render(obj)).rstrip() + "\n")

    def remove(self, obj) -> None:
        if isinstance(obj, Solution):
            shutil.rmtree(self.root / ontology.mirror_dir("solution", id=obj.id), ignore_errors=True)
        elif isinstance(obj, Activity):
            for old in (self.root / mirror_folder("activity")).glob(f"*/{obj.id}.md"):
                old.unlink(missing_ok=True)
        else:
            self.path_for(obj).unlink(missing_ok=True)

    def write_file_text(self, f: SolutionFile, text: str) -> None:
        self._write(self.path_for(f), text)

    def remove_file_text(self, f: SolutionFile) -> None:
        self.path_for(f).unlink(missing_ok=True)

    def rebuild_all(self, db: Session, uploads_dir: Path) -> None:
        """사본 폴더를 비우고 DB 의 모든 항목과 자료 텍스트를 새로 쓴다."""
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        for model in (Solution, Customer, Project, Partner, Activity):
            for obj in db.scalars(select(model).order_by(model.id)):
                self.sync(obj)
        for report in db.scalars(select(ResearchReport).where(ResearchReport.status == "done").order_by(ResearchReport.id)):
            self.sync(report)
        for f in db.scalars(select(SolutionFile).order_by(SolutionFile.id)):
            result = extract_text(Path(uploads_dir) / "solutions" / str(f.solution_id) / f.stored_name)
            f.extract_status = result.status
            if result.status == "ok":
                self.write_file_text(f, file_markdown(f, result.text))
        db.commit()

    # ----- 항목별 본문 -----
    def _activity_log(self, activities: list[Activity]) -> list[str]:
        if not activities:
            return []
        # 최신순, 날짜 없는 활동은 맨 뒤
        ordered = sorted(
            activities,
            key=lambda a: (a.date is not None, a.date.toordinal() if a.date else 0, a.id),
            reverse=True,
        )
        out = ["", f"## {ontology.label('activity')}", ""]
        for a in ordered:
            when = a.date.isoformat() if a.date else "날짜 없음"
            out.append(f"- {when} {a.title or '(제목 없음)'} ({self._rel(a)})")
        return out

    def _linked_activities(self, obj, column) -> list[Activity]:
        session = object_session(obj)
        if session is None:
            return []
        return list(session.scalars(select(Activity).where(column.any(id=obj.id))))

    def _solution(self, s: Solution) -> list[str]:
        out = [f"# {s.name}", ""]
        out += _lines(
            _item(P("solution", "stage"), labels.STAGES.get(s.stage, s.stage)),
            f"- {P('solution', 'partner')}: {self._ref(s.partner.name, s.partner)}" if s.partner else None,
            _item(P("solution", "tags"), s.tags),
            _item(P("solution", "summary"), s.summary),
            _item(P("solution", "usage_direction"), s.usage_direction),
            _item(P("solution", "applicable_types"), s.applicable_types),
        )
        out += _section(P("solution", "description"), s.description)
        if s.files:
            out += ["", f"## {ontology.label('solution_file')}", ""]
            out += [
                f"- {labels.FILE_KINDS.get(f.kind, f.kind)}: {f.original_name} ({self._rel(f)})"
                for f in sorted(s.files, key=lambda f: f.id)
            ]
        return out + self._activity_log(list(s.activities))

    def _customer(self, c: Customer) -> list[str]:
        out = [f"# {c.name}", ""]
        out += _lines(_item("업종", c.industry), _item("연락처", c.contact), _item("메모", c.memo))
        if c.partner is not None:  # 같은 회사인 파트너 (AI 가 경로를 따라갈 수 있게)
            out.append(f"- {ontology.term('same_company')}: {ontology.label('partner')} {self._ref(c.partner.name, c.partner)}")
        if c.projects:
            out += ["", f"## {ontology.label('project')}", ""]
            out += [f"- {self._ref(b.name, b)}" for b in sorted(c.projects, key=lambda b: b.id)]
        return out + self._activity_log(self._linked_activities(c, Activity.customers))

    def _project(self, b: Project) -> list[str]:
        out = [f"# {b.name}", ""]
        out += _lines(
            f"- {P('project', 'customer')}: {self._ref(b.customer.name, b.customer)}" if b.customer else None,
            _item(P("project", "period"), b.period),
            _item(P("project", "status"), b.status),
            _item(P("project", "description"), b.description),
            _item("메모", b.memo),
        )
        return out + self._activity_log(self._linked_activities(b, Activity.projects))

    def _partner(self, p: Partner) -> list[str]:
        out = [f"# {p.name}", ""]
        out += _lines(_item("연락처", p.contact), _item("소개", p.description), _item("메모", p.memo))
        if p.customer is not None:
            out.append(f"- {ontology.term('same_company')}: {ontology.label('customer')} {self._ref(p.customer.name, p.customer)}")
        if p.solutions:
            out += ["", "## 솔루션", ""]
            out += [f"- {self._ref(s.name, s)}" for s in sorted(p.solutions, key=lambda s: s.id)]
        return out + self._activity_log(self._linked_activities(p, Activity.partners))

    def _activity(self, a: Activity) -> list[str]:
        out = [f"# {a.title or '(제목 없음)'}", ""]
        out += _lines(
            _item(P("activity", "kind"), labels.ACTIVITY_KINDS.get(a.kind, a.kind)),
            _item(P("activity", "date"), a.date.isoformat() if a.date else ""),
            _item(ontology.label("solution"), ", ".join(self._ref(s.name, s) for s in a.solutions)),
            _item(ontology.label("customer"), ", ".join(self._ref(c.name, c) for c in a.customers)),
            _item(P("activity", "projects"), ", ".join(self._ref(b.name, b) for b in a.projects)),
            _item(ontology.label("partner"), ", ".join(self._ref(p.name, p) for p in a.partners)),
        )
        out += _section(P("activity", "summary_md"), a.summary_md)
        follow = []
        for fu in a.follow_ups:
            mark = "x" if fu.done else " "
            extra = ", ".join(
                p for p in (
                    f"담당 {fu.owner}" if fu.owner else "",
                    f"기한 {fu.due.isoformat()}" if fu.due else "",
                ) if p
            )
            follow.append(f"- [{mark}] {fu.task}" + (f" ({extra})" if extra else ""))
        out += _section(P("activity", "follow_ups"), "\n".join(follow))
        signals = []
        for n in a.needs:
            who = n.customer_name
            if n.customer is not None:  # 등록된 고객 기록과 연결된 줄
                who = f"{who} ({self._ref(n.customer.name, n.customer)})" if who else self._ref(n.customer.name, n.customer)
            signals.append(f"- {who}: {n.content}" if who else f"- {n.content}")
        out += _section(P("activity", "demand_signals"), "\n".join(signals))
        return out + _section(P("activity", "raw_text"), a.raw_text)

    def _report(self, r: ResearchReport) -> list[str]:
        is_area = r.kind == "area"
        out = [f"# {r.title or ('리서치 보고서' if is_area else '발굴 방향 제안')}", ""]
        out += _lines(
            _item("분야", labels.RESEARCH_AREAS.get(r.area, r.area)) if is_area else _item("종류", "발굴 방향 제안"),
            _item("작성일", r.created_at.date().isoformat() if r.created_at else ""),
        )
        if is_area:
            out += ["", "## 동향"]
            for t in r.trends or []:
                out += ["", f"### {_inline(t.get('headline'))}"]
                if str(t.get("summary") or "").strip():
                    out += [""] + _quote(t["summary"])
                links = [
                    f"- [{_inline(s.get('title')) or _inline(s.get('url'))}]({s.get('url', '')})"
                    + ("" if s.get("opened", True) else " (검색 결과 링크, 본문 미확인)")
                    for s in t.get("sources", [])
                ]
                out += ([""] + links) if links else ["", f"- {checks.NO_LINK_NOTE}"]
        else:
            out += ["", "## 발굴 방향 제안"]
            for sug in r.suggestions or []:
                kind = labels.SUGGESTION_KINDS.get(sug.get("kind"), sug.get("kind", ""))
                out += ["", f"### [{kind}] {sug.get('title', '')}", ""]
                out += _lines(
                    _item("내용", sug.get("detail")),
                    _item(ontology.term("reason"), sug.get("reason")),
                    _item("대상 솔루션 id", ", ".join(str(i) for i in sug.get("solution_ids", []))),
                    _item(ontology.term("source"), ", ".join(sug.get("sources", []))),
                )
        return out + _section("시사점", "\n".join(f"- {_inline(i)}" for i in r.insights or []))


KNOWLEDGE_FORMAT = 4  # 사본의 폴더·제목 형식이 바뀌면 올린다 (2: 프로젝트 폴더 projects/, 통일한 용어. 3: 고객 니즈 줄에 연결된 고객 기록 경로. 4: 파트너·고객의 "같은 회사" 줄)
FORMAT_KEY = "knowledge_format"


def rebuild_if_stale(mirror: KnowledgeMirror, db: Session, uploads_dir: Path) -> bool:
    """사본 형식이 바뀌었거나 옛 폴더(businesses/)가 남아 있으면 사본 전체를 한 번 다시 만든다 (사본이라 지워도 DB 는 그대로).
    다시 만든 뒤 표시를 남겨 두 번 하지 않는다. 실패해도 서버는 뜬다 (`rebuild-knowledge` 로 다시 할 수 있다)."""
    setting = db.get(AppSetting, FORMAT_KEY)
    current = setting is not None and (setting.value or {}).get("version") == KNOWLEDGE_FORMAT
    stale_folders = [f for f in ontology.legacy_mirror_folders() if (mirror.root / f).exists()]
    if current and not stale_folders:
        return False
    try:
        mirror.rebuild_all(db, uploads_dir)
        value = {"version": KNOWLEDGE_FORMAT}
        if setting is None:
            db.add(AppSetting(key=FORMAT_KEY, value=value))
        else:
            setting.value = value
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("지식 사본을 다시 만들지 못했습니다")
        return False
    return True


def get_mirror(request: Request) -> KnowledgeMirror:
    return request.app.state.mirror
