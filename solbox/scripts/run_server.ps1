# 솔박스 서버를 실행한다. 서버가 끝나면 10초 뒤 다시 시작한다 (무한 반복).
# 주의: Linux에서 작성되어 실행 검증이 되지 않았습니다. 처음 실행할 때 내용을 확인하세요.
param(
    # 파이썬 실행 파일. 가상환경을 쓰면 전체 경로를 넘긴다. 예: -PythonExe C:\solbox\.venv\Scripts\python.exe
    [string]$PythonExe = "python",
    # 콘솔 로그 폴더. 생략하면 <저장소>\data\logs
    [string]$LogDir = ""
)
$ErrorActionPreference = "Stop"

# 저장소 폴더 = 이 스크립트가 있는 scripts 폴더의 상위 폴더
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# 파이썬 출력을 UTF-8 로 맞춘다
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 이 스크립트가 서버를 시작했다는 표시: 서버가 끝나면 아래 반복문이 다시 띄워 주므로 관리 > 시스템의 "지금 업데이트"가 서버를 끝내 다시 시작시킬 수 있다.
# 이 표시가 없으면(python -m app 을 직접 실행) 업데이트 버튼은 막힌다. 반드시 python 을 시작하기 전에 둔다.
$env:SOLBOX_SUPERVISED = "1"

# 콘솔 로그: data\logs\server-console.log (앱 로그는 data\logs\app.log).
# config.toml 의 data_dir 를 바꿨다면 -LogDir 로 맞춘다.
$logDir = if ($LogDir) { $LogDir } else { Join-Path $repo "data\logs" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "server-console.log"

while ($true) {
    # 로그가 10MB 를 넘으면 시작할 때마다 server-console.log.1 로 옮긴다 (이전 .1 은 덮어쓴다)
    if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 10MB)) {
        Move-Item -Force -Path $logFile -Destination "$logFile.1"
    }
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 서버를 시작합니다." | Out-File -FilePath $logFile -Append -Encoding utf8
    # 표준 출력과 오류를 한 파일에 덧붙인다. 파이썬이 UTF-8 로 쓰므로 그대로 이어 붙인다.
    & cmd.exe /c "`"$PythonExe`" -m app >> `"$logFile`" 2>&1"
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 서버가 끝났습니다 (종료 코드 $LASTEXITCODE). 10초 뒤 다시 시작합니다." | Out-File -FilePath $logFile -Append -Encoding utf8
    Start-Sleep -Seconds 10
}
