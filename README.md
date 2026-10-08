# 퇴실 QR코드 자동 표시 프로그램

부트캠프 퇴실 체크를 깜빡하는 사람이 없도록, 정해진 시간이 되면 퇴실용 QR코드를
컴퓨터 화면에 전체화면으로 띄워주는 프로그램입니다.

QR은 매주 바뀌므로, **관리자(본인)가 admin-web에서 QR 이미지 한 장만 올리면 설치된
모든 학생 PC가 자동으로 새 QR을 받아갑니다.** 학생 PC에서는 별도 작업이 필요 없습니다.

출석 시스템 QR을 URL이 아니라 **화면 캡처(이미지)로만 얻을 수 있는 경우**를 위해,
관리자는 QR을 URL로 직접 입력하는 대신 **캡처한 QR 이미지 파일을 그대로 업로드**합니다.
학생 프로그램은 그 이미지를 새로 만들지 않고 받은 그대로 화면에 띄웁니다.

## 동작 구조

```
[관리자] admin-web (비밀번호 로그인, QR 이미지 업로드) --> GitHub Gist(JSON, base64 이미지 포함) 갱신
                                                     |
                                                     v (1분마다 자동 조회)
[학생 PC 1..N] main.py --> Gist에서 qr_image(base64) / checkout_time을 읽어와 캐시(cache.json)
                            --> 정해진 시각에 그 QR 이미지를 그대로 전체화면 표시
```

- 학생 PC가 오프라인이거나 Gist 조회에 실패해도 마지막으로 받아온 `cache.json` 값으로 계속 동작합니다.
- Gist는 **공개(public)** 로 만들어도 되며, 읽기는 누구나 가능하지만 **쓰기(갱신)** 는 GitHub 토큰이 있어야 하므로 관리자만 QR을 바꿀 수 있습니다.
- admin-web에서 **실행 요일**(기본 월~금)과, QR 창을 **클릭해서 닫을 때만 자동으로 열릴 링크**(선택, 예: 설문/공지 페이지)도 함께 설정할 수 있습니다. 아무 키나 눌러서 닫으면 링크는 열리지 않습니다.

## 다운로드 링크 (배포용, 고정 주소)

| 용도 | 링크 |
|---|---|
| 학생용 설치 프로그램 (추천) | `https://github.com/hodumaru111/qrcode-checkout/releases/latest/download/QRcodeSetup.exe` |
| 학생용 exe (설치 없이 바로 실행) | `https://github.com/hodumaru111/qrcode-checkout/releases/latest/download/QRcode.exe` + `config.json` |
| 관리자용 (QR 등록/시각 설정) | `https://github.com/hodumaru111/qrcode-checkout/releases/latest/download/QRcodeAdmin.exe` |

**학생용 설치 프로그램(`QRcodeSetup.exe`)** 은 더블클릭하면 설치 마법사가 뜨고,
"Windows 시작 시 자동 실행" 체크박스 하나로 부팅할 때마다 자동으로 대기 상태가 되도록
설정해줍니다. `shell:startup` 폴더에 직접 바로가기를 넣는 수동 작업이 필요 없습니다.
관리자 권한도 필요 없이 사용자 폴더에 설치됩니다.

세 파일 모두 코드를 수정해 push할 때마다 GitHub Actions가 자동으로 다시 빌드해서 같은 링크에 갱신합니다.

---

## 1. 최초 설정 (관리자 1회만)

### 1-1. GitHub Gist 생성
1. https://gist.github.com/ 접속 (본인 GitHub 계정으로 로그인)
2. 파일 이름: `bootcamp_qr_config.json`
3. 내용:
   ```json
   {
     "qr_image": "data:image/png;base64,...",
     "checkout_time": "18:00"
   }
   ```
   (admin-web을 쓰면 이 파일은 웹페이지가 알아서 채워주므로 직접 만들 필요는 없습니다.
   admin-web 없이 Gist를 직접 만드는 경우에만 참고하세요.)
4. **Create public gist** 클릭
5. 생성된 Gist의 URL에서 Gist ID를 확인 (예: `https://gist.github.com/내아이디/abcdef1234567890` → ID는 `abcdef1234567890`)
6. "Raw" 버튼을 눌러 나오는 주소를 복사 (`https://gist.githubusercontent.com/.../raw/bootcamp_qr_config.json` 형태)

### 1-2. GitHub 토큰 발급 (Gist 갱신용)
1. https://github.com/settings/tokens → **Generate new token (classic)**
2. 권한(scope)에서 `gist` 만 체크
3. 생성된 토큰을 안전한 곳에 보관 (다시 볼 수 없으니 주의)

### 1-3. 파이썬 환경 준비
```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 1-4. 학생 배포용 `config.json` 작성
`sync_url`에 위에서 복사한 Raw 주소를 넣습니다. `checkout_url`/`checkout_time`은
Gist 조회가 실패할 때만 쓰이는 예비값이라 대략 맞춰두면 됩니다 (`checkout_url`은
QR 이미지가 아직 한 번도 동기화되지 않았을 때만 쓰이는 URL 기반 예비 QR로, 없어도 됩니다).

```json
{
  "sync_url": "https://gist.githubusercontent.com/내아이디/GIST_ID/raw/bootcamp_qr_config.json",
  "fetch_interval_seconds": 60,
  "checkout_time": "18:00",
  "display_seconds": 600,
  "catchup_minutes": 120,
  "window_title": "퇴실 QR코드 - 스캔 후 아무 키나 눌러 닫기"
}
```

`catchup_minutes`는 절전/늦은 부팅으로 퇴실 시각을 놓쳤을 때 뒤늦게라도 QR을
띄워주는 허용 범위(분)입니다. 기본값 120이며, 정시에만 띄우려면 `0`으로 두세요.

이 `config.json`, `main.py`, `requirements.txt` (또는 exe로 빌드한 실행파일)만
학생들에게 배포하면 됩니다. **`admin_update.py`와 `admin_config.json`은 절대
학생에게 배포하지 마세요.**

---

## 1-2. 프로그램 수정 후 배포 (자동 업데이트)

학생 프로그램은 10분마다 `version.json`을 확인해 (1.9.2 미만은 1시간마다), 버전이 다르면 새 `main.py`를
내려받아 **체크섬 → 문법 → 실제 실행(`--selftest`)** 검증을 모두 통과한 경우에만
교체하고 스스로 재시작합니다. 직전 버전은 `main.py.bak`으로 남습니다.
학생에게 재설치를 다시 안내할 필요가 없습니다.

`main.py`를 고쳤다면:

```bash
# 1. main.py 안의 VERSION 을 올린다 (예: 1.1.0 -> 1.1.1)
# 2. 매니페스트 생성 (selftest가 실패하면 여기서 중단됩니다)
python release.py
# 3. 함께 커밋 & 푸시
git add main.py version.json && git commit -m "..." && git push
```

**비상 중단:** 잘못된 버전을 내보냈다면 `version.json`의 `"enabled"`를 `false`로
바꿔 푸시하세요. 아직 업데이트하지 않은 학생 PC는 그 자리에서 멈춥니다.

---

## 2. 학생 PC에서 실행

개인 노트북에 배포하는 경우 Windows의 **스마트 앱 제어(Smart App Control)/SmartScreen**이
서명되지 않은 `.exe`를 차단할 수 있습니다. 통제할 수 없는 개인 PC에 배포한다면
**소스 배포(권장)** 방식을 사용하세요.

### Windows — 원클릭 설치 (가장 추천, 파일 전달 불필요)
학생이 파일을 다운로드/압축 해제할 필요 없이, **PowerShell에 한 줄만 붙여넣으면**
자동으로 설치됩니다.

1. `Win + X` → **터미널**(또는 Windows PowerShell) 실행
2. 아래 명령어 붙여넣고 Enter

```powershell
irm https://qrcode-checkout.vercel.app/install.ps1 | iex
```

이 한 줄이 자동으로:
- `%LOCALAPPDATA%\qrcode-checkout`에 최신 `main.py`/`config.json`/`requirements.txt` 다운로드
- Python 설치 여부 확인 (없으면 설치 링크 안내 후 종료)
- 가상환경 생성 + 패키지 설치
- Windows 시작프로그램 자동 등록
- 설치 직후 바로 백그라운드 대기 시작

Python이 없는 학생은 https://www.python.org/downloads/ 에서 설치하면서
**"Add python.exe to PATH"를 반드시 체크**한 뒤 명령어를 다시 실행하면 됩니다.

### Windows — zip 파일 배포 (대안)
PowerShell 명령어 실행이 부담스러운 경우, 다음 파일들을 압축해서 전달할 수도 있습니다.
- `main.py`, `requirements.txt`, `config.json`
- `student_setup.bat`, `run_silent.vbs`

학생은 압축을 풀고 **`student_setup.bat`을 더블클릭**하면 됩니다 (동작은 `install.ps1`과 동일).

### Windows — 설치 프로그램(exe) 사용
위 "다운로드 링크" 표의 `QRcodeSetup.exe`를 받아 더블클릭 → 설치 마법사에서
"Windows 시작 시 자동 실행" 체크 → 설치 완료. 이후 부팅할 때마다 자동으로 대기 상태가 됩니다.
단, 서명되지 않은 exe라 스마트 앱 제어/SmartScreen에 막힐 수 있습니다
(막히면 위 소스 배포 방식을 사용하세요).

### 소스로 직접 실행 (개발/테스트용)
```bash
python3 main.py
```

매일 Gist에 등록된 시각에 자동으로 QR이 전체화면으로 뜹니다. 종료는 `Ctrl+C`.
바로 테스트하려면:

```bash
python3 main.py --test-now
```

### 부팅 시 자동 실행을 수동으로 설정해야 할 때
- **Windows (exe를 설치 프로그램 없이 직접 배포한 경우)**: exe와 `config.json`을 같은 폴더에 두고
  → `Win+R` → `shell:startup` → 폴더에 exe 바로가기 추가
- **macOS**: 로그인 항목(시스템 설정 > 일반 > 로그인 항목)에 `python3 main.py` 실행
  스크립트 등록, 또는 `launchd` 사용

---

## 3. 관리자: 매주 QR 갱신하기

**관리자가 나 혼자가 아니라 매기수 다른 매니저로 바뀔 수 있다면 [admin-web](admin-web/)을
쓰세요.** GitHub 계정이나 토큰 없이 비밀번호만으로 웹페이지에서 QR을 갱신할 수 있고,
매니저가 바뀌면 비밀번호만 바꾸면 됩니다. 배포 방법은 [admin-web/README.md](admin-web/README.md) 참고.

아래 exe/GUI/CLI 방식은 **내 GitHub 계정의 개인 토큰**이 필요해서, 그 계정을 계속
쓸 수 있는 한 사람(주로 본인)이 관리할 때만 적합합니다.

> **참고**: 아래 exe/GUI/CLI 도구는 아직 "QR URL 텍스트 입력" 방식만 지원합니다.
> 출석 시스템의 QR을 **URL 없이 이미지로만** 받는다면 admin-web의 이미지 업로드
> 기능을 사용하세요 (위 "매니저 교체 시" 안내 참고).

### exe로 실행 (Python 설치 없이)
학생용과 마찬가지로 관리자용도 고정 링크로 빌드되어 있습니다.

- **관리자용 다운로드**: `https://github.com/hodumaru111/qrcode-checkout/releases/latest/download/QRcodeAdmin.exe`

다운로드한 `QRcodeAdmin.exe`를 더블클릭하면 아래 GUI 설명과 동일하게 동작합니다.
(`config.json`은 필요 없습니다 — Gist ID/토큰은 프로그램 안에서 직접 입력)

### GUI로 갱신 (소스로 직접 실행)
```bash
source venv/bin/activate
python3 admin_gui.py
```
1. 최초 실행 시 비밀번호 설정 화면이 뜹니다 (6자 이상) → 설정 후 프로그램을 다시 실행해 로그인
2. 비밀번호 입력 후 "잠금 해제"
3. Gist ID / 파일 이름 / GitHub 토큰 입력 (필요하면 "이 컴퓨터에 토큰 저장" 체크 → 다음부터 비밀번호만 입력)
4. "현재 값 불러오기"로 지금 등록된 QR URL·시각 확인
5. URL/시각을 수정하고 "Gist에 반영" 클릭 → 즉시 반영, 학생 PC는 다음 동기화 주기(기본 1분)에 자동으로 받아감

### CLI로 갱신
```bash
source venv/bin/activate
python3 admin_update.py
```
- 최초 실행 시 관리자 비밀번호를 설정합니다 (6자 이상).
- 이후 실행할 때마다 비밀번호를 확인한 뒤, Gist ID / 파일명 / GitHub 토큰을 물어봅니다
  (한 번 입력하면 `admin_config.json`에 로컬 저장되어 다음부터는 비밀번호만 입력하면 됩니다).
- 새 퇴실 QR URL과 (필요하면) 시각을 입력하면 Gist가 즉시 갱신됩니다.

명령줄 인자로 바로 갱신할 수도 있습니다:

```bash
python3 admin_update.py --url "https://새로운-퇴실-QR-주소" --time "18:00"
```

### 비밀번호 재설정
```bash
python3 admin_update.py --set-password
```
(GUI는 재설정 시 `admin_config.json`에서 `password_hash`, `salt`를 지우고 다시 실행하면 최초 설정 화면이 뜹니다.)

### 보안 주의사항
- `QRcodeAdmin.exe`/`admin_gui.py`/`admin_update.py` 자체는 코드에 비밀번호나 토큰이 들어있지 않아
  공개 배포해도 안전합니다 (비밀번호·토큰은 실행 후 직접 입력).
- 다만 실행하면 그 컴퓨터에 `admin_config.json`(비밀번호 해시, 저장했다면 GitHub 토큰)이 생성됩니다.
  **이 파일은 절대 다른 사람과 공유하거나 git에 올리지 마세요** (`.gitignore`에 이미 제외되어 있습니다).
- GitHub 토큰은 `gist` 권한만 부여하세요.

---

## 4. 배포용 exe / 설치 프로그램 빌드 (선택 — 보통은 필요 없음)

GitHub Actions가 push할 때마다 `QRcode.exe`, `QRcodeAdmin.exe`, `QRcodeSetup.exe`를
자동으로 빌드해 "다운로드 링크" 표의 고정 주소에 올려주므로, 평소에는 이 섹션을 볼 일이
없습니다. 로컬에서 직접 빌드해봐야 할 때만 아래를 참고하세요.

학생들에게 Python 설치 없이 나눠주고 싶다면 PyInstaller로 실행파일을 만듭니다.

**중요: PyInstaller는 빌드를 실행한 운영체제용 실행파일만 만듭니다 (크로스 컴파일 불가).**
학생들 대부분이 Windows를 쓴다면 **Windows PC에서** `build.bat`을 실행해야 진짜 `.exe`가 나옵니다.
macOS에서 빌드하면 macOS용 실행파일(`.app`)만 만들어지고 Windows에서는 실행되지 않습니다.

### Windows에서 빌드 (.exe)
Windows PC에서 이 프로젝트 폴더를 열고:
```
build.bat
```
더블클릭하거나 명령 프롬프트에서 실행하면 `dist\QRcode.exe`가 생성됩니다.

### macOS/Linux에서 빌드 (해당 OS용, 배포는 보통 필요 없음)
```bash
./build.sh
```

### 배포
빌드가 끝나면 `dist` 폴더의 실행파일과 `config.json`을 함께 전달하세요.
(`admin_update.py`, `admin_config.json`은 포함하지 않습니다.)
