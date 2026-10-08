# 퇴실 QR코드 프로그램 - 자가진단 스크립트
# 사용법: irm https://qrcode-checkout.vercel.app/check.ps1 | iex
# QR이 안 뜬 학생이 실행하면 원인을 바로 알 수 있습니다.

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$InstallDir = Join-Path $env:LOCALAPPDATA "qrcode-checkout"
$StartupVbs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\qrcode-checkout.vbs"
$SyncUrl = "https://gist.githubusercontent.com/hodumaru111/5cd259614734afe93651c086fdcad554/raw/bootcamp_qr_config.json"

$problems = @()

function Write-Check($label, $ok, $detail) {
    $mark = if ($ok) { "  OK  " } else { " 문제 " }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host "[$mark] " -ForegroundColor $color -NoNewline
    Write-Host "$label — $detail"
}

Write-Host ""
Write-Host "=== 퇴실 QR 프로그램 자가진단 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "이 PC 이름: " -NoNewline
Write-Host "$env:COMPUTERNAME" -ForegroundColor Yellow
Write-Host "(매니저가 이 PC에만 지정 공지를 보내야 할 때 이 이름을 알려주세요)" -ForegroundColor DarkGray
Write-Host ""

# 1. 설치 여부
$installed = Test-Path (Join-Path $InstallDir "main.py")
Write-Check "설치" $installed $(if ($installed) { $InstallDir } else { "설치 폴더가 없습니다" })
if (-not $installed) { $problems += "프로그램이 설치되어 있지 않습니다. 설치 명령을 다시 실행하세요." }

# 2. 실행 여부
$running = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*qrcode-checkout*' })
Write-Check "실행 중" ($running.Count -gt 0) $(if ($running.Count -gt 0) { "$($running.Count)개 프로세스 (1개 인스턴스는 보통 2개로 보입니다)" } else { "지금 돌고 있지 않습니다" })
if ($running.Count -eq 0) { $problems += "프로그램이 실행되고 있지 않습니다. 재설치하거나 재부팅해보세요." }

# 3. 프로그램 버전 (구버전이면 요일 설정 등 최신 기능이 없다)
$localVer = $null
if ($installed) {
    $m = Select-String -Path (Join-Path $InstallDir "main.py") -Pattern '^VERSION = "([^"]+)"' -Encoding UTF8 |
         Select-Object -First 1
    if ($m) { $localVer = $m.Matches[0].Groups[1].Value }
}
$latestVer = $null
try {
    $latestVer = (Invoke-WebRequest -Uri "https://raw.githubusercontent.com/hodumaru111/qrcode-checkout/main/version.json?t=$([int](Get-Date -UFormat %s))" -UseBasicParsing -TimeoutSec 10).Content |
                 ConvertFrom-Json | Select-Object -ExpandProperty version
} catch {}

if (-not $localVer) {
    Write-Check "프로그램 버전" $false "구버전 (요일 설정 등 최신 기능이 없습니다)"
    $problems += "오래된 버전입니다. 주말에도 QR이 뜨거나 설정이 반영되지 않습니다. 재설치가 필요합니다."
} elseif ($latestVer -and $localVer -ne $latestVer) {
    Write-Check "프로그램 버전" $false "$localVer (최신 $latestVer)"
    $problems += "최신 버전이 아닙니다. 켜져 있으면 1시간 안에 자동으로 업데이트되지만, 급하면 재설치하세요."
} else {
    Write-Check "프로그램 버전" $true $(if ($localVer) { "$localVer (최신)" } else { "확인 불가" })
}

# 4. 시작프로그램 등록
$startupOk = Test-Path $StartupVbs
Write-Check "자동 시작 등록" $startupOk $(if ($startupOk) { "등록됨" } else { "등록 안 됨 — 부팅해도 자동 실행되지 않습니다" })
if (-not $startupOk) { $problems += "시작프로그램에 등록되어 있지 않습니다. 설치 명령을 다시 실행하세요." }

# 5. 중앙 설정 접근 가능 여부 (학교 방화벽/프록시에 막히는 경우가 있음)
$remoteTime = $null
try {
    $r = Invoke-WebRequest -Uri "$SyncUrl`?t=$([int](Get-Date -UFormat %s))" -UseBasicParsing -TimeoutSec 10
    $cfg = $r.Content | ConvertFrom-Json
    $remoteTime = $cfg.checkout_time
    Write-Check "중앙 설정 연결" $true "정상 — 퇴실 시각 $remoteTime, 요일 $($cfg.active_days -join ',')"
} catch {
    Write-Check "중앙 설정 연결" $false "차단됨 ($($_.Exception.Message))"
    $problems += "GitHub 연결이 막혀 최신 퇴실 시각을 못 받아옵니다. 다른 와이파이(휴대폰 핫스팟 등)로 바꿔보세요."
}

# 6. 이 PC가 실제로 쓰고 있는 시각
$localTime = $null
$cachePath = Join-Path $InstallDir "cache.json"
$configPath = Join-Path $InstallDir "config.json"
foreach ($f in @($cachePath, $configPath)) {
    if ((-not $localTime) -and (Test-Path $f)) {
        try { $localTime = (Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json).checkout_time } catch {}
    }
}
if ($localTime) {
    $match = ($null -eq $remoteTime) -or ($localTime -eq $remoteTime)
    $localDays = $null
    foreach ($f in @($cachePath, $configPath)) {
        if ((-not $localDays) -and (Test-Path $f)) {
            try { $localDays = (Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json).active_days } catch {}
        }
    }
    if ($localDays) { Write-Check "이 PC의 실행 요일" $true ($localDays -join ',') }
    Write-Check "이 PC의 퇴실 시각" $match $(if ($match) { $localTime } else { "$localTime (중앙 설정 $remoteTime 과 다릅니다)" })
    if (-not $match) { $problems += "이 PC가 오래된 시각($localTime)을 쓰고 있습니다. 재설치하면 맞춰집니다." }
}

# 7. PC 시계 (시각이 틀어져 있으면 엉뚱한 때 뜹니다)
$tz = (Get-TimeZone).Id
$clockOk = $tz -eq "Korea Standard Time"
Write-Check "PC 시계" $clockOk "$(Get-Date -Format 'yyyy-MM-dd HH:mm') / $tz"
if (-not $clockOk) { $problems += "시간대가 한국(Korea Standard Time)이 아닙니다. Windows 설정에서 시간대를 확인하세요." }

# 8. 최근 기록
$logPath = Join-Path $InstallDir "qrcode.log"
if (Test-Path $logPath) {
    Write-Host ""
    Write-Host "--- 최근 기록 (마지막 15줄) ---" -ForegroundColor Cyan
    Get-Content $logPath -Tail 15 -Encoding UTF8
} else {
    Write-Host ""
    Write-Host "(아직 기록 파일이 없습니다. 구버전이라면 재설치 후 생깁니다.)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== 진단 결과 ===" -ForegroundColor Cyan
if ($problems.Count -eq 0) {
    Write-Host "문제를 찾지 못했습니다. 정상 동작 중입니다." -ForegroundColor Green
    Write-Host "그래도 QR이 안 떴다면, 퇴실 시각에 노트북이 꺼져 있었을 가능성이 큽니다." -ForegroundColor Gray
    Write-Host "(최신 버전은 이후 2시간 안에 켜면 뒤늦게라도 한 번 띄워줍니다.)" -ForegroundColor Gray
} else {
    foreach ($p in $problems) { Write-Host " - $p" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "대부분은 아래 재설치 한 줄로 해결됩니다:" -ForegroundColor Gray
    Write-Host "  irm https://qrcode-checkout.vercel.app/install.ps1 | iex" -ForegroundColor White
}
Write-Host ""
Write-Host "이 화면을 통째로 캡처해서 매니저에게 보내주세요." -ForegroundColor Gray
Write-Host ""
