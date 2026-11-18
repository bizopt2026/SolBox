# 솔루션 박스

솔루션 발굴 과정(아이템 접수 → 사전 태핑 → 수요조사 → 박스 등록)과 파트너·고객·기존 사업·활동 기록을 한곳에 모으는 사내 웹 앱입니다. AI(Claude CLI)는 메모 정리와 소개 초안 작성을 돕는 보조 기능이며, AI가 동작하지 않아도 조회와 직접 입력은 그대로 쓸 수 있습니다.

## 준비물

- Python 3.11 이상
- (AI 기능을 쓸 때) Claude CLI. **앱을 실행하는 PC에 설치하고, 앱을 실행하는 Windows 계정과 같은 계정으로 로그인**해 두어야 합니다. 다른 PC나 다른 계정에서 로그인한 것은 소용없습니다.

## 설치

프로젝트 폴더에서 PowerShell을 열고 아래 명령을 하나씩 실행합니다.

```powershell
pip install -e .
```

## 설정 파일 만들기

예시 파일을 복사해 `config.toml` 을 만듭니다.

```powershell
copy config.example.toml config.toml
```

메모장 등으로 `config.toml` 을 열어 다음을 확인합니다.

- `secret_key`: **반드시 길고 무작위한 값으로 바꾸세요.** 기본값 `change-me` 그대로 쓰면 로그인 세션을 누구나 위조할 수 있습니다.
- `data_dir`: 데이터(`app.db`, `uploads/`, `knowledge/`)를 저장할 폴더. 상대 경로는 `config.toml` 이 있는 폴더 기준입니다.
- `host`, `port`: 기본은 `127.0.0.1`, `8443` 입니다. 다른 PC에서도 접속하게 하려면 `host = "0.0.0.0"` 으로 바꿉니다.
- `ssl_certfile`, `ssl_keyfile`: 인증서와 키 파일 경로. 둘 다 적으면 HTTPS로, 적지 않으면 HTTP로 열립니다.

## 관리자 계정 만들기

처음 한 번, 관리자 계정을 만듭니다. 아이디·이름·비밀번호는 원하는 값으로 바꾸세요.

```powershell
python -m app.cli create-admin --username admin --name 관리자 --password 원하는비밀번호
```

일반 사용자는 관리자로 로그인한 뒤 화면 위쪽 "관리 → 사용자"에서 추가합니다.

## 실행

```powershell
python -m app
```

브라우저에서 아래 주소를 엽니다.

- 인증서를 설정하지 않았을 때: http://127.0.0.1:8443
- `ssl_certfile`, `ssl_keyfile` 을 설정했을 때: https://127.0.0.1:8443 (자체 서명 인증서는 브라우저 경고가 나올 수 있습니다)

포트나 주소를 바꿨다면 `config.toml` 의 값에 맞춰 열면 됩니다. 종료는 실행 창에서 `Ctrl+C` 입니다.

## knowledge 폴더 다시 만들기

`data_dir` 아래 `knowledge/` 는 DB 내용을 AI가 읽도록 옮겨 둔 사본입니다. 사본이 지워졌거나 어긋났다면 DB를 기준으로 통째로 다시 만들 수 있습니다. 올려 둔 자료 파일의 텍스트도 원본에서 다시 뽑습니다.

```powershell
python -m app.cli rebuild-knowledge
```

## 테스트 실행

```powershell
pip install -e ".[dev]"
```

```powershell
python -m pytest -q
```

(`[dev]` 항목이 없다면 `pip install pytest httpx` 로 대신합니다.)

## 실제 Claude CLI 수동 확인 목록

자동 테스트는 가짜 CLI로 돌립니다. 실제 PC에 설치한 Claude CLI로는 아래를 한 번 직접 확인하세요.

1. `claude --help` 에 `-p`, `--output-format`, `--allowedTools` 가 있는지 확인합니다. 이름이 다르면 `app/ai/runner.py` 의 명령 구성을 맞춥니다.
2. 활동 기록에 메모를 붙여 넣고 "AI로 정리" → 초안이 나오고 저장되는지 확인합니다.
3. 솔루션에 자료 파일을 올리고 "AI로 소개 초안 만들기" → 초안이 채워지는지 확인합니다.
4. 두 브라우저에서 동시에 요청 → 한쪽에 "AI 대기 2번째" 가 보이는지 확인합니다.
5. Claude CLI에서 로그아웃한 뒤 요청 → 로그인 안내문이 보이고, 목록 조회와 수동 저장은 정상인지 확인합니다.
