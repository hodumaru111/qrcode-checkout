"""배포용 version.json을 생성한다.

main.py를 고쳐 학생 PC에 배포하려면:

    1. main.py의 VERSION을 올린다
    2. python release.py
    3. main.py와 version.json을 함께 커밋 & 푸시

학생 프로그램은 1시간마다 version.json을 확인해서, 버전이 다르면 main.py를
내려받아 체크섬/문법/실행 검증을 모두 통과한 경우에만 교체하고 재시작한다.

version.json의 "enabled"를 false로 바꾸고 푸시하면 전체 자동 업데이트가 즉시
멈춘다 (잘못된 버전을 내보냈을 때의 비상 스위치).
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
MAIN_PY = ROOT / "main.py"
VERSION_JSON = ROOT / "version.json"
ASSET_DIR = ROOT / "assets"
MAX_ASSET_BYTES = 3_000_000  # main.py의 MAX_ASSET_BYTES와 같아야 한다 (넘으면 학생 PC가 거부한다)
RAW_BASE = "https://raw.githubusercontent.com/sungho19141935-cyber/qrcode-checkout/main"


def main():
    source = MAIN_PY.read_bytes()

    match = re.search(r'^VERSION = "([^"]+)"', source.decode("utf-8"), re.MULTILINE)
    if not match:
        sys.exit("main.py에서 VERSION을 찾지 못했습니다.")
    version = match.group(1)

    # 학생 PC가 적용 전에 하는 검사를 여기서 먼저 돌려, 깨진 버전이 나가지 않게 한다
    try:
        compile(source, "main.py", "exec")
    except SyntaxError as e:
        sys.exit(f"main.py에 문법 오류가 있습니다: {e}")

    result = subprocess.run(
        [sys.executable, str(MAIN_PY), "--selftest"], capture_output=True, timeout=60
    )
    if result.returncode != 0:
        sys.exit(f"selftest 실패, 배포를 중단합니다:\n{result.stderr.decode('utf-8', 'replace')}")

    # 학생 PC는 raw.githubusercontent.com이 주는 바이트를 그대로 해싱한다. git이
    # Windows 작업트리에 CRLF로 체크아웃해도 raw는 항상 LF로 서빙하므로, 여기서도
    # LF로 정규화한 내용의 해시를 기록해야 양쪽이 일치한다.
    normalized = source.replace(b"\r\n", b"\n")
    if normalized != source:
        print(f"(작업트리가 CRLF입니다. LF 기준으로 체크섬을 계산합니다.)")

    manifest = {
        "version": version,
        "url": f"{RAW_BASE}/main.py",
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "enabled": True,
    }
    intro = ASSET_DIR / "intro.webp"
    if intro.exists():
        data = intro.read_bytes()  # 바이너리라 git/raw가 바이트를 바꾸지 않는다 (줄바꿈 정규화 불필요)
        if len(data) > MAX_ASSET_BYTES:
            sys.exit(f"assets/intro.webp가 {len(data)} bytes입니다. 학생 프로그램은 {MAX_ASSET_BYTES} 초과 파일을 받지 않습니다.")
        # 학생 PC가 저장 전에 하는 검사와 같다: 실제로 열리고, 프레임이 둘 이상이어야 한다
        try:
            from PIL import Image
            import io

            im = Image.open(io.BytesIO(data))
            im.load()
            frames = getattr(im, "n_frames", 1)
        except Exception as e:
            sys.exit(f"assets/intro.webp를 열 수 없습니다: {e}")
        if frames < 2:
            sys.exit("assets/intro.webp에 프레임이 하나뿐입니다 (영상이 아닙니다).")
        fps = 12
        meta = ASSET_DIR / "intro.json"
        if meta.exists():
            fps = json.loads(meta.read_text(encoding="utf-8-sig")).get("fps", fps)
        manifest["assets"] = {
            "intro": {
                "url": f"{RAW_BASE}/assets/intro.webp",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "fps": fps,
            }
        }
        print(f"인트로 영상 포함: {frames}프레임, {len(data) // 1024}KB, {fps}fps")

    VERSION_JSON.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"version.json 생성 완료 (v{version}, sha256 {manifest['sha256'][:12]}...)")
    print("main.py, version.json, assets/를 함께 커밋해야 합니다.")


if __name__ == "__main__":
    main()
