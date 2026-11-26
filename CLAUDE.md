# 저장소 안내

이 저장소는 솔박스(`solbox/`, 사내 웹 앱: 솔루션 발굴, 활동 기록, AI 제안서, 주간 리서치) 저장소예요. 작업 규칙은 `solbox/CLAUDE.md`(도구 분리·지식 사본·데이터 위치 규칙, 실행·테스트 명령)에 있어요.

- 사용자와는 한국어로 대화해요.
- 솔박스를 고칠 때는 반드시 `solbox/CLAUDE.md` 규칙을 따르고, 명령은 `solbox/` 폴더 안에서 실행해요.
- 개인 앱 써브쓰리·밑줄은 저장소 `bizopt2026/artifact` 로 커밋 기록과 함께 옮겼고, 오답노트는 지웠어요(2026-11-26). 이 저장소에 개인 앱을 다시 두지 않아요.

## 운영 규칙 (main = 회사 PC 솔박스 배포)

회사 PC 는 push 하지 않고 main 을 pull 만 해서 솔박스(`solbox/`)를 운영해요. 개발·테스트는 모두 클라우드 세션에서 해요.

- **main 은 회사 PC 솔박스의 운영 배포 브랜치예요.** main 에 커밋이 들어가는 순간 회사 PC 의 관리 › 시스템 "시스템 업데이트" 버튼에 새 버전으로 떠요(`solbox/app/update.py` 가 `origin/main` 을 확인). 솔박스와 상관없는 커밋(루트 문서 등)도 새 커밋 수에 세어져요.
- **작업은 `claude/*` 브랜치에서 해요.** main 에 바로 커밋하지 않아요.
- **solbox 를 건드렸으면 `solbox/` 안에서 테스트를 통과시킨 뒤에만 main 에 병합해요:** `cd solbox && python -m pytest -q -W error -n auto` (경고도 오류, 병렬 실행, `slow` 표시 시험까지 전부). GitHub Actions(`.github/workflows/solbox-tests.yml`)도 같은 테스트를 돌려요.
- **병합한 `claude/*` 브랜치는 워크플로가 지워요** (`.github/workflows/cleanup-merged-branches.yml`: main 에 push 될 때마다, 끝 커밋이 main 에 다 들어 있는 `claude/*` 브랜치만 지움). 클라우드 세션의 git 프록시는 원격 브랜치 삭제를 막으므로(HTTP 403) 세션에서 `git push origin --delete` 를 하지 않아요. 그 밖의 브랜치는 GitHub 화면(Branches)에서 지워요.
- **병합 전에 사용자에게 먼저 알려요:** solbox 의 DB 마이그레이션(`solbox/app/migrate.py`, 모델 컬럼 추가·변경)이나 패키지(`solbox/pyproject.toml`) 변경이 들어 있을 때. 회사 PC 업데이트 때 백업과 `pip install -e` 가 일어나요 (pyproject.toml 이 바뀌면 pip 설치, 업데이트마다 백업).
- **회사 PC 는 sparse-checkout 으로 `solbox/` 만 받아요**(cone 모드라 루트 파일 `CLAUDE.md`·`README.md`·`.gitignore` 는 같이 받아져요). 그래서 솔박스가 `solbox/` 밖 파일에 기대게 만들지 않아요.
