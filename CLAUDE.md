# 작업 규칙 (이 저장소)

- 앱 본체는 `app/index.html` 하나이고, claude.ai 아티팩트 https://claude.ai/artifact/S65CCJg77Gy9NKmqZ1bzzQ 로 게시해요.
- 사용자와는 한국어로 대화해요.
- **기능을 고쳐 게시할 때마다 버전을 올려요.** 규칙은 `docs/VERSIONING.md`에 있어요.
  - 고칠 곳: `APP_VERSION`, `APP_RELEASED`, `VLOG` 맨 앞 항목
  - 범위: MAJOR(호환 깨짐·구조 개편) / MINOR(새 기능) / PATCH(수정·다듬기)
  - 커밋 메시지 맨 앞에 `vX.Y.Z`를 붙이고, `docs/VERSIONING.md`의 변경 기록 표에도 한 줄 추가해요.
- 복제본용 업데이트 프롬프트는 `docs/prompts/YYYY-MM-DD.md`로 **하루에 한 파일**로 정리하고, `docs/prompts/README.md` 표에 한 줄 추가해요. 프롬프트에는 "버전 값(APP_VERSION·VLOG)은 바꾸지 마"를 꼭 넣어요.
