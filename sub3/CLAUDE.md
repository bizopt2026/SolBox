# 작업 규칙 (써브쓰리 · `sub3/` 폴더)

- 앱 이름은 **써브쓰리**예요(예전 이름: Sub-3 훈련 체크). 화면·문서·프롬프트에는 새 이름을 써요.

- 이 앱의 파일은 모두 저장소의 `sub3/` 폴더 안에 있어요. 아래 경로는 `sub3/` 기준이에요(명령은 `sub3/`에서 실행).
- 앱 본체는 `app/index.html` 하나이고, claude.ai 아티팩트 https://claude.ai/artifact/S65CCJg77Gy9NKmqZ1bzzQ 로 게시해요.
- 사용자와는 한국어로 대화해요.
- **기능을 고쳐 게시할 때마다 버전을 올려요.** 규칙은 `docs/VERSIONING.md`에 있어요.
  - 고칠 곳: `APP_VERSION`, `APP_RELEASED`, `VLOG` 맨 앞 항목
  - 범위: MAJOR(호환 깨짐·구조 개편) / MINOR(새 기능) / PATCH(수정·다듬기)
  - 커밋 메시지 맨 앞에 `vX.Y.Z`를 붙이고, `docs/VERSIONING.md`의 변경 기록 표에도 한 줄 추가해요.
- **버전을 올릴 때마다 그 버전의 복제본 프롬프트를 자동으로 만들어 저장해요.**
  - `APP_VERSION`·`VLOG`를 올린 뒤(커밋 전) `python3 tools/version_prompt.py`를 실행해요. 직전 버전 커밋과 비교해 `docs/prompts/versions/vX.Y.Z.md`를 만들고, 이전 파일에 적용해 결과가 같은지 스스로 검증해요. 목록은 `docs/prompts/versions/README.md`에 자동으로 추가돼요.
  - 검증이 실패하면(버전 영역과 겹치는 변경 등) 손으로 정리해요. 만든 파일은 앱 변경과 같은 커밋에 넣어요.
- **복제본 업데이트(npm):** main 에 `app/index.html`이 들어오면 GitHub Actions(`.github/workflows/sub3-npm.yml`)가 `npm/build.mjs`로 npm 패키지 `sub3@<APP_VERSION>`을 올리고, 복제한 앱은 가이드 → 버전 관리 → '업데이트 확인'으로 그 버전을 받아 자기 자신을 다시 게시해요(`artifact` 권한). 그래서 `app/index.html`을 바꾸면 **버전을 꼭 올려서** main 에 병합해야 복제본에 전달돼요. 원본 아티팩트를 게시할 때 권한 선언에 `artifact`를 빼지 않아요. 자세한 내용은 `docs/VERSIONING.md` '복제한 앱'.
- 날짜별 묶음 프롬프트도 함께 관리해요: 복제본용 업데이트 프롬프트는 `docs/prompts/YYYY-MM-DD.md`로 **하루에 한 파일**로 정리하고, `docs/prompts/README.md` 표에 한 줄 추가해요. 프롬프트에는 "버전 값(APP_VERSION·VLOG)은 바꾸지 마"를 꼭 넣어요.
- **개발 md 파일은 항상 내려받을 수 있게 해요.**
  - 버전을 올려 게시할 때마다, 그날 날짜별 프롬프트(`docs/prompts/YYYY-MM-DD.md`)와 그 버전 프롬프트(`docs/prompts/versions/vX.Y.Z.md`)를 **대화에 파일로 보내요**(파일 카드에서 바로 열기·내려받기).
  - 커밋은 작업 브랜치에 바로 푸시해서 GitHub에서도 같은 파일을 볼 수 있게 해요.
  - 사용자가 전체를 원하면 `docs/prompts` 전체를 zip으로 묶어(`exports/`, 저장소에는 넣지 않음) 보내요.
