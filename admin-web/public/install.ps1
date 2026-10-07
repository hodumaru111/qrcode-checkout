# 퇴실 QR코드 프로그램 - 학생용 원클릭 설치 스크립트
# 사용법: PowerShell에서 아래 한 줄만 실행
#   irm https://raw.githubusercontent.com/hodumaru111/qrcode-checkout/main/install.ps1 | iex

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$RepoRaw = "https://raw.githubusercontent.com/hodumaru111/qrcode-checkout/main"
$InstallDir = Join-Path $env:LOCALAPPDATA "qrcode-checkout"
$Files = @("main.py", "requirements.txt", "config.json")

function Write-Step($msg) {
    Write-Host ""
    Write-Host $msg -ForegroundColor Cyan
}

Write-Step "[1/6] 기존에 실행 중인 프로그램 정리 중..."
# 재설치 시 구버전 프로세스가 계속 살아 있으면 새로 띄운 인스턴스와 둘 다 동작해
# 퇴실 시각에 QR 창이 두 개 뜬다. 또 실행 중인 venv 파일은 덮어쓰기가 막힌다.
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*qrcode-checkout*' }
if ($running) {
    $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $running | ForEach-Object { Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue }
    Write-Host "    -> 이전 버전 $($running.Count)개를 종료했습니다."
} else {
    Write-Host "    -> 실행 중인 이전 버전 없음"
}

Write-Step "[2/6] 설치 폴더 준비 중... ($InstallDir)"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Step "[3/6] 최신 프로그램 파일 다운로드 중..."
foreach ($f in $Files) {
    $dest = Join-Path $InstallDir $f
    if ($f -eq "config.json" -and (Test-Path $dest)) {
        # config.json은 이미 있으면 덮어쓰지 않음 (재설치/업데이트 시 기존 설정 보존)
        continue
    }
    Invoke-WebRequest -Uri "$RepoRaw/$f" -OutFile $dest -UseBasicParsing
}

Write-Step "[4/6] Python 설치 확인 중..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host ""
    Write-Host "[오류] Python이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host "https://www.python.org/downloads/ 에서 Python을 설치한 뒤" -ForegroundColor Yellow
    Write-Host "설치 화면에서 'Add python.exe to PATH'를 꼭 체크하고 이 명령어를 다시 실행해주세요." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "아무 키나 누르면 종료합니다"
    exit 1
}
Write-Host "    -> OK"

Write-Step "[5/6] 가상환경 및 패키지 설치 중... (시간이 걸릴 수 있습니다)"
Push-Location $InstallDir
if (-not (Test-Path "venv\pyvenv.cfg")) {
    # 이전 설치가 중간에 끊겨 venv가 불완전하게 남아있을 수 있으므로 통째로 새로 만든다
    Remove-Item "venv" -Recurse -Force -ErrorAction SilentlyContinue
    python -m venv venv
}
& ".\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& ".\venv\Scripts\pip.exe" install --quiet -r requirements.txt
Pop-Location
Write-Host "    -> OK"

Write-Step "[6/6] Windows 시작프로그램 등록 중..."
$vbsPath = Join-Path $InstallDir "run_silent.vbs"
$pythonwPath = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
$mainPyPath = Join-Path $InstallDir "main.py"
# 경로를 스크립트 자기 위치 기준(GetParentFolderName)으로 계산하면, 이 파일을
# 시작프로그램 폴더에 복사했을 때 그 폴더를 설치 폴더로 착각해 실행이 깨진다.
# 그래서 설치 경로를 아예 하드코딩해서 어디에 복사해도 항상 정확히 동작하게 한다.
@"
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "$InstallDir"
shell.Run """$pythonwPath"" ""$mainPyPath""", 0, False
"@ | Set-Content -Path $vbsPath -Encoding ASCII

$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
Copy-Item -Path $vbsPath -Destination (Join-Path $startupDir "qrcode-checkout.vbs") -Force
Write-Host "    -> 등록 완료: 다음 부팅부터 자동으로 대기 상태가 됩니다."

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " 설치가 완료되었습니다!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "지금 바로 백그라운드 대기를 시작합니다..."
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbsPath`""
Write-Host "완료! 컴퓨터를 재부팅해도 자동으로 다시 시작됩니다."
