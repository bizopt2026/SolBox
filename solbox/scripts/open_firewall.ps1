# 솔박스 포트를 Windows 방화벽에서 연다 (인바운드 TCP, 규칙 이름 "SolutionBox"). 관리자 권한이 필요하다.
# 주의: Linux에서 작성되어 실행 검증이 되지 않았습니다. 처음 실행할 때 내용을 확인하세요.
param(
    [ValidateRange(1, 65535)][int]$Port,   # config.toml 의 port 와 같은 값 (-Remove 와 함께 쓸 때는 생략해도 된다)
    [switch]$Remove                              # 규칙을 삭제한다
)
$ErrorActionPreference = "Stop"

$ruleName = "SolutionBox"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "관리자 권한 PowerShell 에서 실행하세요."
}

if (-not $Remove -and -not $PSBoundParameters.ContainsKey("Port")) {
    throw "-Port 를 지정하세요. 예: -Port 8443"
}

# 같은 이름의 규칙이 있으면 먼저 지운다 (포트를 바꿔 다시 실행해도 규칙이 하나만 남는다)
$existing = @(Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)
if ($existing.Count -gt 0) { $existing | Remove-NetFirewallRule }

if ($Remove) {
    if ($existing.Count -gt 0) {
        Write-Host "방화벽 규칙 '$ruleName' 을 삭제했습니다."
    } else {
        Write-Host "방화벽 규칙 '$ruleName' 이 없었습니다. 할 일이 없습니다."
    }
    return
}

New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private | Out-Null
Write-Host "방화벽 규칙 '$ruleName' 을 추가했습니다 (TCP $Port, 도메인·개인 네트워크)."
