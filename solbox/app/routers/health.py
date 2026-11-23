"""가벼운 상태 확인 (로그인 없이): 업데이트 뒤 다시 시작을 기다리는 화면이 부른다. 버전과 서버가 시작될 때의 커밋만 알린다."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import update
from app.system import app_version

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
def healthz():
    return JSONResponse({"ok": True, "version": app_version(), "commit": update.boot_commit()["sha"]}, headers={"Cache-Control": "no-store"})
