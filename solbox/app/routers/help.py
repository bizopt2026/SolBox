"""도움말 화면: 업무 흐름 그림, 용어 풀이(온톨로지에서 만든다), 이럴 땐 이렇게, 단축키."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app import intake as intake_lib
from app import ontology
from app.main import render
from app.models import User
from app.security import require_user

router = APIRouter()


def glossary() -> list[dict]:
    """용어 풀이: 이름·뜻은 대상 유형 정의에서 그대로 (정의가 바뀌면 도움말도 따라온다). 목록 화면이 있으면 그 주소도."""
    out = []
    for code in ontology.HELP_GLOSSARY:
        t = ontology.TYPES[code]
        out.append({"code": code, "label": t.label, "text": t.description, "url": t.list_url})
    return out


def stage_blocks() -> list[dict]:
    """"발굴 단계"·"영업기회 단계" 풀이: 이름과 한 문장 뜻은 코드 목록(ontology.CODE_LISTS)에서 그대로."""
    out = []
    for key, flow, side in (("stage", ontology.STAGE_FLOW, (ontology.STAGE_HOLD,)), ("opportunity_stage", ontology.OPPORTUNITY_FLOW, ontology.OPPORTUNITY_SIDE_STAGES)):
        cl = ontology.CODE_LISTS[key]
        out.append({"key": key, "title": cl.label, "rows": [(cl.items[c], ontology.code_help(key, c)) for c in (*flow, *side)]})
    cl = ontology.CODE_LISTS["business_type"]  # 사업방식 (베타 Y): 단계가 아니라 고르는 값이지만 같은 모양으로 풀어 준다
    out.append({"key": "business_type", "title": cl.label, "rows": [(name, ontology.code_help("business_type", code)) for code, name in cl.items.items()]})
    return out


@router.get("/help")
def help_page(request: Request, user: User = Depends(require_user)):
    return render(request, "help.html", glossary=glossary(), stage_blocks=stage_blocks(), relation_words=ontology.RELATION_WORDS, is_admin=user.role == "admin", max_files=intake_lib.MAX_FILES)
