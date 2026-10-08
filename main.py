import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import qrcode
from PIL import Image, ImageTk

CONFIG_PATH = Path(__file__).parent / "config.json"
CACHE_PATH = Path(__file__).parent / "cache.json"
LOG_PATH = Path(__file__).parent / "qrcode.log"
LOG_MAX_BYTES = 512_000
BACKUP_PATH = Path(__file__).parent / "main.py.bak"

VERSION = "1.9.1"
DEFAULT_UPDATE_URL = (
    "https://raw.githubusercontent.com/hodumaru111/qrcode-checkout/main/version.json"
)
# GitHub 계정 이름 변경 이력 (예전 이름 -> 새 이름). 저장소 주소는 GitHub이 새 주소로
# 넘겨주지만, Gist raw 주소는 계정 이름이 바뀌면 그대로 404가 난다. 이미 설치된 PC의
# config.json(설치 스크립트가 덮어쓰지 않는다)에 남은 예전 주소를 여기서 고쳐 쓴다.
ACCOUNT_RENAMES = {"sungho19141935-cyber": "hodumaru111"}
DEFAULT_UPDATE_INTERVAL = 3600  # 1시간마다 확인
# 관리자 페이지 '설치 현황'에 보이는 상태 보고. GitHub 계정 이름과 무관한 주소를 쓴다.
DEFAULT_STATUS_URL = "https://qrcode-checkout.vercel.app/api/heartbeat"
DEFAULT_STATUS_INTERVAL = 6 * 3600  # 켜질 때 한 번 + 6시간마다
MIN_MAIN_PY_BYTES = 5_000  # 이보다 작으면 잘린 응답으로 간주

DEFAULT_CHECKOUT_TIME = "18:00"
DEFAULT_ACTIVE_DAYS = ["mon", "tue", "wed", "thu", "fri"]
DEFAULT_CLOSE_LOCK_SECONDS = 3  # QR이 뜬 직후 이 시간 동안은 키/클릭으로 닫히지 않는다
# 인트로 영상이 끝난 직후의 잠금. 영상이 도는 5초가 이미 입력을 막아 주므로 기본은 0.
DEFAULT_LOCK_AFTER_INTRO_SECONDS = 0
ASSET_DIR = Path(__file__).parent / "assets"
MAX_ASSET_BYTES = 3_000_000  # 인트로 영상 크기 상한 (이보다 크면 받지 않는다)
DEFAULT_INTRO_FPS = 12
DEFAULT_CATCHUP_MINUTES = 120  # 절전/부팅 지연으로 정시를 놓쳤을 때 뒤늦게라도 띄우는 허용 범위
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # datetime.weekday() 순서


def log(message: str):
    """화면과 로그 파일에 함께 기록한다.

    pythonw.exe로 실행하면 콘솔이 없어 print 출력이 전부 사라진다. QR이 안 떴을 때
    동기화 실패인지, 애초에 실행이 안 된 것인지 구분하려면 파일 기록이 필요하다.
    """
    print(message)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            # 오래된 절반을 버리고 최근 기록만 남긴다 (파일 무한 증가 방지)
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-LOG_MAX_BYTES // 2 :]
            LOG_PATH.write_text(tail, encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 로그를 못 남겨도 프로그램은 계속 돌아야 한다


def load_config():
    # 편집기나 스크립트가 BOM을 붙여 저장해도 읽히도록 utf-8-sig로 연다
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def load_cache():
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8-sig") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(data: dict):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def migrate_url(url):
    """예전 GitHub 계정 이름이 들어간 주소를 새 이름으로 바꾼다. 해당 없으면 그대로."""
    if not url:
        return url
    for old, new in ACCOUNT_RENAMES.items():
        url = url.replace(f"/{old}/", f"/{new}/")
    return url


# 마지막 설정 받기 결과. 상태 보고에 실어 보내, 관리자가 "설정을 못 받는 PC"를 알 수 있게 한다.
SYNC_STATUS = {"error": ""}


def fetch_remote_config(sync_url: str, timeout: int = 10) -> Optional[dict]:
    """관리자가 갱신하는 중앙 설정(Gist 등)을 가져온다. 실패하면 None."""
    try:
        # raw.githubusercontent.com은 CDN에서 몇 분간 응답을 캐시하므로,
        # 캐시 버스팅 쿼리를 붙여 항상 최신 내용을 받아온다.
        sep = "&" if "?" in sync_url else "?"
        busted_url = f"{sync_url}{sep}t={int(time.time())}"
        req = urllib.request.Request(busted_url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        has_schedule = isinstance(data.get("schedule"), list) and data["schedule"]
        if not has_schedule and (
            "checkout_time" not in data or not ("qr_image" in data or "checkout_url" in data)
        ):
            log("[QRcode] 원격 설정에 checkout_time과 qr_image(또는 checkout_url)가 필요합니다. 무시합니다.")
            SYNC_STATUS["error"] = "설정 형식 오류"
            return None
        SYNC_STATUS["error"] = ""
        return data
    except Exception as e:
        # 예전에는 URLError 등 일부만 잡아서, 서버가 응답 도중 연결을 끊으면
        # (RemoteDisconnected, IncompleteRead) 예외가 루프 밖으로 나가 프로그램이 종료됐다.
        log(f"[QRcode] 원격 설정 갱신 실패 (마지막 캐시 사용): {e}")
        SYNC_STATUS["error"] = f"{type(e).__name__}: {e}"[:160]
        return None


def make_qr_image(url: str, box_size: int = 10):
    qr = qrcode.QRCode(box_size=box_size, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def decode_qr_image(qr_image_b64: str):
    """관리자가 업로드한 QR 이미지(base64)를 디코딩한다. 'data:image/png;base64,' 접두사도 허용."""
    b64 = qr_image_b64.split(",", 1)[-1] if "," in qr_image_b64 else qr_image_b64
    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def get_display_image(state: dict):
    """표시할 이미지를 만든다. 공지 항목에 이미지가 없으면 None (문구만 띄운다).

    공지는 퇴실 QR과 무관한 안내이므로, 이미지를 안 붙였다고 퇴실 QR을 대신
    띄우면 학생이 엉뚱한 QR을 찍게 된다. 그래서 공지는 물려받지 않는다.
    """
    qr_image_b64 = state.get("qr_image")
    if qr_image_b64:
        try:
            return decode_qr_image(qr_image_b64)
        except (ValueError, binascii.Error, OSError) as e:
            log(f"[QRcode] 저장된 이미지를 열지 못했습니다: {e}")

    if state.get("kind") == "notice":
        return None

    return make_qr_image(state.get("checkout_url", ""))


def intro_frame_index(elapsed: float, fps: float, n_frames: int) -> Optional[int]:
    """경과 시간으로 지금 보여줄 프레임 번호를 구한다. 영상이 끝났으면 None.

    프레임 수를 세는 대신 경과 시간으로 계산하므로, 느린 PC에서 프레임이 밀려도
    영상 전체가 정해진 시간 안에 끝난다.
    """
    if fps <= 0 or n_frames <= 0:
        return None
    idx = int(elapsed * fps)
    return idx if idx < n_frames else None


def load_intro(state: dict) -> Optional[dict]:
    """재생할 인트로 영상 정보 {"path", "fps"}. 꺼져 있거나 파일이 없으면 None."""
    if state.get("intro_enabled") is False:
        return None
    path = ASSET_DIR / "intro.webp"
    if not path.exists():
        return None
    fps = DEFAULT_INTRO_FPS
    try:
        meta = ASSET_DIR / "intro.json"
        if meta.exists():
            fps = float(json.loads(meta.read_text(encoding="utf-8-sig")).get("fps", fps))
    except (OSError, ValueError):
        pass
    return {"path": str(path), "fps": fps}


def intro_for_entry(entry: dict, state: dict) -> Optional[dict]:
    """퇴실 항목에만 영상을 튼다. 공지는 자막이 퇴실용이라 틀지 않는다."""
    if entry.get("kind") == "notice":
        return None
    return load_intro(state)


def play_intro(root, intro: dict, on_done, later=None) -> bool:
    """루트 창 안에서 영상을 틀고, 끝나면 on_done()을 부른다.

    열지 못하면 아무것도 그리지 않고 False를 돌려준다 (호출한 쪽이 QR로 넘어간다).
    later(ms, fn)은 타이머 예약 함수다. 창을 닫을 때 한꺼번에 취소하려고 호출한 쪽에서 넘긴다.
    """
    later = later or root.after
    try:
        im = Image.open(intro["path"])
        n_frames = getattr(im, "n_frames", 1)
        if n_frames < 2:
            return False
        w, h = im.size
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        scale = min(screen_h * 0.80 / h, screen_w * 0.60 / w)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
    except Exception as e:  # 손상된 파일 등 — 영상 없이 QR로 간다
        log(f"[QRcode] 인트로 영상을 열지 못했습니다 (건너뜀): {e}")
        return False

    frame = tk.Frame(root, bg="white")
    frame.pack(fill="both", expand=True)
    label = tk.Label(frame, bg="white")
    label.place(relx=0.5, rely=0.5, anchor="center")
    shown = {"idx": -1, "photo": None}
    started = time.time()

    def finish():
        frame.destroy()
        on_done()

    def tick():
        idx = intro_frame_index(time.time() - started, intro["fps"], n_frames)
        if idx is None:
            finish()
            return
        if idx != shown["idx"]:
            try:
                im.seek(idx)
                resized = im.convert("RGB").resize(size, Image.Resampling.BILINEAR)
                shown["photo"] = ImageTk.PhotoImage(resized)  # 참조를 들고 있어야 사라지지 않는다
                label.configure(image=shown["photo"])
                shown["idx"] = idx
            except Exception as e:  # 재생 도중 깨지면 거기서 멈추고 QR로
                log(f"[QRcode] 인트로 재생 중 오류 (QR로 넘어감): {e}")
                finish()
                return
        later(30, tick)

    tick()
    return True


def show_qr_window(
    state: dict,
    title: str,
    display_seconds: int,
    lock_seconds: int = DEFAULT_CLOSE_LOCK_SECONDS,
    intro: Optional[dict] = None,
    lock_after_intro: int = DEFAULT_LOCK_AFTER_INTRO_SECONDS,
):
    """전체화면 QR(또는 공지) 창.

    intro가 있으면 먼저 영상을 틀고(그동안 키/클릭은 무시) 끝나면 같은 창에서 QR을 보여준다.
    영상이 없으면 QR이 뜬 직후 lock_seconds 동안 입력을 무시한다.
    """
    img = get_display_image(state)
    if img is not None:
        img.thumbnail((700, 700))
    message = (state.get("message") or "").strip()

    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    root.attributes("-fullscreen", True)
    root.configure(bg="white")

    hint = (
        "QR 스캔 후 아무 키나 누르거나 화면을 클릭하면 닫힙니다."
        if img is not None
        else "아무 키나 누르거나 화면을 클릭하면 닫힙니다."
    )
    refs = {}  # PhotoImage 참조 유지용
    hint_label = {}
    phase = {"intro": False}
    lock = {"on": False}
    timers = []

    def later(ms, fn):
        timers.append(root.after(ms, fn))

    def shutdown():
        # 창을 닫은 뒤에도 예약된 타이머가 남아 있으면, 다음 창을 띄울 때 실행되며
        # "invalid command name" 오류가 난다. 닫기 전에 전부 취소한다.
        for t in timers:
            try:
                root.after_cancel(t)
            except Exception:
                pass
        root.destroy()

    def close(_event=None):
        shutdown()

    def build_content():
        tk.Label(root, text=title, font=("Malgun Gothic", 24, "bold"), bg="white").pack(
            pady=(40, 10)
        )
        if img is not None:
            refs["photo"] = ImageTk.PhotoImage(img)
            tk.Label(root, image=refs["photo"], bg="white").pack(expand=True)
            # 문구는 이미지를 가리지 않도록 아래에 둔다
            if message:
                tk.Label(
                    root,
                    text=message,
                    font=("Malgun Gothic", 18),
                    bg="white",
                    fg="#1f2328",
                    wraplength=1000,
                    justify="center",
                ).pack(pady=(4, 0))
        else:
            # 이미지 없는 공지: 문구만 화면 가운데에 크게
            tk.Label(
                root,
                text=message,
                font=("Malgun Gothic", 34, "bold"),
                bg="white",
                fg="#1f2328",
                wraplength=1100,
                justify="center",
            ).pack(expand=True)
        hint_label["w"] = tk.Label(root, text=hint, font=("Malgun Gothic", 14), bg="white", fg="gray")
        hint_label["w"].pack(pady=(10, 40))

    def unlock():
        lock["on"] = False
        hint_label["w"].config(text=hint)

    def show_content(lock_secs):
        build_content()
        # 학생이 작업(타이핑) 중일 때 QR이 뜨면, 치던 키 때문에 QR을 보기도 전에 닫혀버린다.
        # 뜬 직후 잠깐은 키/클릭을 무시하고, 끝나면 안내 문구를 바꾼다.
        if lock_secs > 0:
            lock["on"] = True
            hint_label["w"].config(text="잠시 후부터 닫을 수 있습니다...")
            later(lock_secs * 1000, unlock)

    def close_by_key(_event=None):
        if not phase["intro"] and not lock["on"]:
            close()

    def close_by_click(_event=None):
        if phase["intro"] or lock["on"]:
            return
        shutdown()
        after_close_url = state.get("after_close_url")
        if after_close_url:
            webbrowser.open(after_close_url)

    def finish_intro():
        phase["intro"] = False
        show_content(lock_after_intro)

    root.bind("<Key>", close_by_key)
    root.bind("<Button-1>", close_by_click)
    # 시간이 다 돼서 자동으로 닫히는 것은 잠금/영상과 무관하게 항상 동작한다
    later(display_seconds * 1000, close)

    if intro:
        phase["intro"] = True
        if not play_intro(root, intro, finish_intro, later):
            phase["intro"] = False
            show_content(lock_seconds)  # 영상을 못 틀었으면 예전처럼 QR 잠금을 쓴다
    else:
        show_content(lock_seconds)

    root.mainloop()


def parse_hhmm(value: str) -> Optional[int]:
    """HH:MM 문자열을 자정 기준 분으로 변환. 형식이 틀리면 None."""
    try:
        hh, mm = value.split(":")
        minutes = int(hh) * 60 + int(mm)
    except (AttributeError, ValueError):
        return None
    return minutes if 0 <= minutes < 24 * 60 else None


def normalize_times(value) -> list:
    """설정에서 읽은 퇴실 시각을 정렬된 HH:MM 목록으로 만든다.

    문자열 하나("18:00"), 쉼표로 이어진 문자열("12:00,18:00"), 리스트를 모두 받는다.
    형식이 틀린 값은 버린다.
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    out = []
    for item in items:
        text = str(item).strip()
        if parse_hhmm(text) is not None and text not in out:
            out.append(text)
    return sorted(out, key=parse_hhmm)


def times_of(state: dict) -> list:
    """state에서 쓸 퇴실 시각 목록. 구 설정(checkout_time 하나)도 그대로 동작한다."""
    times = normalize_times(state.get("checkout_times"))
    if times:
        return times
    return normalize_times(state.get("checkout_time"))


def current_pc_name() -> str:
    try:
        return socket.gethostname().strip()
    except OSError:
        return ""


def entry_applies_here(entry: dict) -> bool:
    """target_pc가 비어 있으면 전원 대상, 채워져 있으면 그 PC에서만 True."""
    target = (entry.get("target_pc") or "").strip()
    if not target:
        return True
    return target.lower() == current_pc_name().lower()


def schedule_of(state: dict) -> list:
    """시각별 설정 목록을 [{time, qr_image, checkout_url, after_close_url}, ...]로 만든다.

    새 형식(schedule)이 있으면 그것을, 없으면 구 형식(checkout_times + 공용 qr_image)을
    같은 모양으로 변환해 돌려준다. 항목에 이미지가 없으면 공용 이미지를 물려받는다.
    """
    def entry(time_text, source):
        kind = "notice" if source.get("kind") == "notice" else "checkout"
        own_image = source.get("qr_image")
        # 퇴실 항목만 기본 QR을 물려받는다. base_qr_image는 기본 QR 전용 칸이고,
        # qr_image는 구버전 호환용이라 마지막 항목의 이미지가 들어갈 수 있어 뒤에 둔다.
        inherited = state.get("base_qr_image") or state.get("qr_image")
        return {
            "time": time_text,
            "kind": kind,
            "qr_image": own_image or (None if kind == "notice" else inherited),
            "message": source.get("message") or "",
            # 공지에만 쓰는 선택 항목: 비워두면 전원에게, 채우면 그 PC에만 표시된다.
            # 퇴실(checkout)에는 의도적으로 지원하지 않는다 — 퇴실 QR을 한 명에게만
            # 지정하면 그 순간 나머지 전원이 QR을 못 받게 되기 때문이다.
            "target_pc": (source.get("target_pc") or "").strip() if kind == "notice" else "",
            "checkout_url": source.get("checkout_url") or state.get("checkout_url", ""),
            "after_close_url": (
                source.get("after_close_url")
                if source.get("after_close_url") is not None
                else state.get("after_close_url")
            ),
        }

    # 키를 (시각, 대상PC)로 둔다. 시각만으로 키를 잡으면 "같은 시각, 다른 PC
    # 대상"으로 등록한 두 공지가 서로를 덮어써 한쪽이 조용히 사라진다.
    by_key = {}
    raw = state.get("schedule")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            time_text = str(item.get("time", "")).strip()
            if parse_hhmm(time_text) is not None:
                built = entry(time_text, item)
                by_key[(time_text, built["target_pc"])] = built

    if not by_key:  # 구 형식: 모든 시각이 같은 QR을 공유 (대상 지정 없음)
        for time_text in times_of(state):
            by_key[(time_text, "")] = entry(time_text, state)

    return [by_key[k] for k in sorted(by_key, key=lambda k: (parse_hhmm(k[0]), k[1]))]


def due_time(now: datetime, times: list, done_today: set, catchup_minutes: int) -> Optional[str]:
    """지금 띄워야 할 시각을 고른다. 없으면 None.

    여러 시각을 한꺼번에 놓친 경우(노트북을 오래 꺼둔 경우) 창을 여러 개 띄우지 않고
    가장 최근에 지난 것 하나만 띄운다. 나머지는 호출한 쪽에서 소진 처리한다.
    """
    candidates = [t for t in times if t not in done_today and should_trigger(now, t, catchup_minutes)]
    return candidates[-1] if candidates else None


def should_trigger(now: datetime, checkout_time: str, catchup_minutes: int) -> bool:
    """정시에 정확히 일치할 때만이 아니라, 정시를 지난 뒤 catchup_minutes 안이면 True.

    15초 간격 폴링이라도 노트북이 절전에 들어가거나 부팅이 늦으면 해당 1분을
    통째로 건너뛰어 그날 QR이 아예 뜨지 않는다. 지난 시각도 따라잡도록 한다.
    """
    target = parse_hhmm(checkout_time)
    if target is None:
        return False
    current = now.hour * 60 + now.minute
    return 0 <= current - target <= catchup_minutes


def verify_asset(data: bytes, expected_sha256: str, max_bytes: int = MAX_ASSET_BYTES) -> Optional[str]:
    """내려받은 영상 파일을 저장해도 되는지 검사한다. 문제가 있으면 사유, 없으면 None."""
    if not data:
        return "빈 파일입니다"
    if len(data) > max_bytes:
        return f"파일이 너무 큽니다 ({len(data)} bytes)"
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        return f"체크섬 불일치 (기대 {expected_sha256[:12]}..., 실제 {actual[:12]}...)"
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        if getattr(im, "n_frames", 1) < 2:
            return "프레임이 하나뿐입니다 (영상이 아닙니다)"
    except Exception as e:
        return f"이미지로 열 수 없습니다: {e}"
    return None


def sync_assets(manifest: dict) -> None:
    """version.json이 가리키는 인트로 영상을 내려받아 둔다. 실패해도 조용히 넘어간다.

    영상은 main.py 교체와 별개라서, 버전이 같아도 매번 확인한다. 이미 같은 파일이
    있으면 아무것도 받지 않는다.
    """
    asset = (manifest.get("assets") or {}).get("intro")
    if not isinstance(asset, dict):
        return
    url = asset.get("url")
    expected = str(asset.get("sha256") or "").lower()
    if not url or len(expected) != 64:
        return
    fps = asset.get("fps") or DEFAULT_INTRO_FPS
    dest = ASSET_DIR / "intro.webp"
    meta = ASSET_DIR / "intro.json"

    try:
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == expected:
            return
    except OSError:
        pass

    try:
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(
            f"{url}{sep}t={int(time.time())}", headers={"Cache-Control": "no-cache"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(MAX_ASSET_BYTES + 1)  # 상한을 넘는 응답은 끝까지 읽지 않는다
    except Exception as e:  # 응답 도중 끊김(IncompleteRead) 등도 여기서 멈춘다
        log(f"[QRcode] 인트로 영상 내려받기 실패 (무시): {e}")
        return

    problem = verify_asset(data, expected)
    if problem:
        log(f"[QRcode] 인트로 영상 거부 - {problem}")
        return

    try:
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".webp.new")
        tmp.write_bytes(data)
        os.replace(tmp, dest)  # 재생 중인 파일을 반쯤 쓴 채로 두지 않는다
        meta.write_text(json.dumps({"fps": fps, "sha256": expected}), encoding="utf-8")
        log(f"[QRcode] 인트로 영상 저장 ({len(data) // 1024}KB, {fps}fps)")
    except OSError as e:
        log(f"[QRcode] 인트로 영상 저장 실패 (무시): {e}")


def build_status(state: dict) -> dict:
    """관리자 '설치 현황'에 보낼 내용. 화면이나 파일 내용은 보내지 않는다."""
    return {
        "app": "qrcode-checkout",
        "pc": current_pc_name()[:64],
        "version": VERSION,
        "last_shown": (state.get("last_shown") or "")[:32],
        "times": ", ".join(e["time"] for e in schedule_of(state))[:120],
        "sync_ok": (state.get("last_sync_ok") or "")[:32],
        "sync_error": (SYNC_STATUS.get("error") or "")[:160],
    }


def post_status(status_url: str, payload: dict, timeout: int = 10) -> bool:
    """상태를 보고한다. 실패해도 프로그램 동작에는 영향이 없다."""
    try:
        req = urllib.request.Request(
            status_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = resp.status == 200
        log("[QRcode] 상태 보고 완료" if ok else f"[QRcode] 상태 보고 응답 {resp.status}")
        return ok
    except Exception as e:  # 부가 기능이므로 어떤 실패든 넘어간다
        log(f"[QRcode] 상태 보고 실패 (무시): {e}")
        return False


def fetch_json(url: str, timeout: int = 10):
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{sep}t={int(time.time())}", headers={"Cache-Control": "no-cache"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_new_source(source: bytes, expected_sha256: str) -> Optional[str]:
    """새 main.py를 적용해도 되는지 검사한다. 문제가 있으면 사유 문자열, 없으면 None."""
    if len(source) < MIN_MAIN_PY_BYTES:
        return f"파일이 너무 작습니다 ({len(source)} bytes) - 응답이 잘린 것으로 보입니다"

    actual = hashlib.sha256(source).hexdigest()
    if actual != expected_sha256:
        return f"체크섬 불일치 (기대 {expected_sha256[:12]}..., 실제 {actual[:12]}...)"

    try:
        compile(source, "main.py", "exec")
    except SyntaxError as e:
        return f"문법 오류: {e}"

    if b"def run_scheduler" not in source:
        return "run_scheduler가 없습니다 - 올바른 프로그램 파일이 아닙니다"

    return None


def smoke_test(source: bytes) -> Optional[str]:
    """새 코드를 실제로 한 번 실행해본다. 정상 종료하지 못하면 사유를 돌려준다.

    문법만 통과하고 실행 즉시 죽는 버전으로 교체해버리면 학생 PC에서 프로그램이
    조용히 사라진다. 교체 전에 --selftest로 한 번 띄워보고 통과한 것만 적용한다.
    """
    tmp_dir = tempfile.mkdtemp(prefix="qrcode_update_")
    tmp_main = Path(tmp_dir) / "main.py"
    try:
        tmp_main.write_bytes(source)
        # 설정 파일도 같이 둬야 새 코드가 config를 읽을 수 있다
        try:
            tmp_main.with_name("config.json").write_text(
                CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass

        result = subprocess.run(
            [sys.executable, str(tmp_main), "--selftest"],
            capture_output=True,
            timeout=60,
            cwd=tmp_dir,
        )
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-300:]
            return f"실행 검증 실패 (종료코드 {result.returncode}) {detail}"
        return None
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"실행 검증 중 오류: {e}"
    finally:
        try:
            for f in Path(tmp_dir).iterdir():
                f.unlink(missing_ok=True)
            Path(tmp_dir).rmdir()
        except OSError:
            pass


def apply_update(source: bytes) -> bool:
    """main.py를 교체한다. 직전 버전은 main.py.bak으로 남긴다."""
    me = Path(__file__).resolve()
    tmp = me.with_suffix(".py.new")
    try:
        tmp.write_bytes(source)
        try:
            BACKUP_PATH.write_bytes(me.read_bytes())
        except OSError as e:
            log(f"[QRcode] 백업 실패, 업데이트를 중단합니다: {e}")
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, me)  # 같은 볼륨 내 원자적 교체
        return True
    except OSError as e:
        log(f"[QRcode] 업데이트 적용 실패: {e}")
        tmp.unlink(missing_ok=True)
        return False


def restart_self():
    """교체된 코드로 새 프로세스를 띄우고 현재 프로세스는 종료한다."""
    me = str(Path(__file__).resolve())
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, me], cwd=str(Path(me).parent), creationflags=flags, close_fds=True
    )
    sys.exit(0)


def check_for_update(update_url: str) -> bool:
    """새 버전이 있으면 검증 후 교체한다. 교체했으면 True (호출자가 재시작)."""
    try:
        manifest = fetch_json(update_url)
    except Exception as e:
        log(f"[QRcode] 업데이트 확인 실패 (무시하고 계속): {e}")
        return False

    if manifest.get("enabled") is False:
        return False  # 긴급 중단 스위치

    try:
        sync_assets(manifest)  # 버전이 같아도 영상은 매번 확인한다. 실패해도 업데이트는 계속한다
    except Exception as e:
        log(f"[QRcode] 영상 동기화 오류 (무시): {e}")

    remote_version = str(manifest.get("version", ""))
    if not remote_version or remote_version == VERSION:
        return False

    source_url = manifest.get("url")
    expected_sha = str(manifest.get("sha256", ""))
    if not source_url or not expected_sha:
        log("[QRcode] 업데이트 정보에 url/sha256이 없어 건너뜁니다.")
        return False

    log(f"[QRcode] 새 버전 발견: {VERSION} -> {remote_version}, 내려받는 중...")
    try:
        sep = "&" if "?" in source_url else "?"
        req = urllib.request.Request(
            f"{source_url}{sep}t={int(time.time())}", headers={"Cache-Control": "no-cache"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            source = resp.read()
    except Exception as e:
        log(f"[QRcode] 새 버전 내려받기 실패: {e}")
        return False

    problem = verify_new_source(source, expected_sha)
    if problem:
        log(f"[QRcode] 업데이트 거부 - {problem}")
        return False

    problem = smoke_test(source)
    if problem:
        log(f"[QRcode] 업데이트 거부 - {problem}")
        return False

    # 디스크의 main.py가 이미 새 버전인 경우(다른 인스턴스가 먼저 교체했거나, 재시작에
    # 실패해 구버전이 메모리에 남아있는 경우)에는 다시 쓰지 않는다. 그대로 덮어쓰면
    # 멀쩡한 이전 버전 백업이 같은 내용으로 지워진다.
    try:
        if Path(__file__).resolve().read_bytes().replace(b"\r\n", b"\n") == source:
            log(f"[QRcode] 파일은 이미 v{remote_version}입니다. 재시작만 합니다.")
            return True
    except OSError:
        pass

    if not apply_update(source):
        return False

    log(f"[QRcode] 업데이트 완료: {VERSION} -> {remote_version}. 재시작합니다.")
    return True


def shown_today_from_log(today: str) -> set:
    """로그에서 오늘 이미 띄운 퇴실 시각을 찾는다.

    1.9.0 전 버전은 '오늘 띄운 시각'을 파일에 남기지 않았다. 그 버전에서 오늘 QR을 띄운 뒤
    1.9.0으로 업데이트되어 재시작하면, 기록이 없어 따라잡기 시간 안에 QR이 한 번 더 뜬다.
    표시할 때마다 남기는 로그 줄("... (설정 17:50) - QR 화면 표시")은 예전 버전부터 같아서
    거기서 복원한다.
    """
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    found = set()
    for line in text.splitlines():
        if line.startswith(today) and "화면 표시" in line:
            m = re.search(r"\(설정 (\d{2}:\d{2})\)", line)
            if m:
                found.add(m.group(1))
    return found


def run_scheduler(config):
    sync_url = migrate_url(config.get("sync_url"))
    if sync_url != config.get("sync_url"):
        log(f"[QRcode] 동기화 주소를 바뀐 GitHub 계정 이름으로 고쳐 씁니다: {sync_url}")
    fetch_interval = int(config.get("fetch_interval_seconds", 300))
    display_seconds = int(config.get("display_seconds", 600))
    close_lock_seconds = max(0, int(config.get("close_lock_seconds", DEFAULT_CLOSE_LOCK_SECONDS)))
    lock_after_intro = max(
        0, int(config.get("close_lock_after_intro_seconds", DEFAULT_LOCK_AFTER_INTRO_SECONDS))
    )
    window_title = config.get("window_title", "퇴실 QR코드")
    catchup_minutes = int(config.get("catchup_minutes", DEFAULT_CATCHUP_MINUTES))
    update_url = migrate_url(config.get("update_url", DEFAULT_UPDATE_URL))
    update_interval = int(config.get("update_check_seconds", DEFAULT_UPDATE_INTERVAL))
    status_url = config.get("status_url", DEFAULT_STATUS_URL)
    status_interval = int(config.get("status_interval_seconds", DEFAULT_STATUS_INTERVAL))

    state = load_cache()
    state.setdefault("checkout_url", config.get("checkout_url", ""))
    state.setdefault("qr_image", config.get("qr_image"))
    state.setdefault("checkout_time", config.get("checkout_time", DEFAULT_CHECKOUT_TIME))
    state.setdefault("checkout_times", config.get("checkout_times"))
    state.setdefault("schedule", config.get("schedule"))
    state.setdefault("intro_enabled", config.get("intro_enabled", True))
    state.setdefault("base_qr_image", config.get("base_qr_image"))
    state.setdefault("active_days", config.get("active_days", DEFAULT_ACTIVE_DAYS))
    state.setdefault("after_close_url", config.get("after_close_url"))

    # 오늘 이미 띄운 시각은 cache.json에 남긴다. 메모리에만 두면 업데이트로 재시작하거나
    # 퇴실 후 재부팅했을 때 기록이 사라져, 따라잡기 시간 안이면 QR이 한 번 더 뜬다.
    triggered_date = state.get("done_date")  # done_today가 어느 날짜의 기록인지
    done_today = set(state.get("done_times") or []) if triggered_date else set()  # 오늘 이미 띄운 시각들
    _today = datetime.now().strftime("%Y-%m-%d")
    if triggered_date != _today:
        recovered = shown_today_from_log(_today)
        if recovered:
            triggered_date, done_today = _today, recovered
            log(f"[QRcode] 오늘 이미 띄운 시각을 로그에서 확인: {', '.join(sorted(recovered))} (다시 띄우지 않음)")
    last_fetch = 0.0
    last_update_check = 0.0
    last_status = 0.0  # 0이면 켜지자마자(첫 설정 받기 직후) 한 번 보고한다
    synced_once = False

    log(
        f"[QRcode] 시작 v{VERSION} - 예정 시각 "
        f"{', '.join(e['time'] for e in schedule_of(state)) or '없음'}, "
        f"요일 {state.get('active_days')}, 따라잡기 {catchup_minutes}분"
    )
    if sync_url:
        log(f"[QRcode] 중앙 설정 동기화 사용: {sync_url} ({fetch_interval}초마다 갱신)")

    while True:
        # 한 바퀴에서 어떤 예외가 나도 기록만 하고 다음 바퀴로 넘어간다.
        # 여기서 프로그램이 죽으면 다음 재부팅까지 QR이 안 뜨고, 학생은 알 방법이 없다.
        try:
            now_ts = time.time()

            # QR을 띄우는 중에 교체가 끼어들지 않도록, 표시 직전이 아닐 때만 확인한다
            if update_url and now_ts - last_update_check >= update_interval:
                last_update_check = now_ts
                if check_for_update(update_url):
                    restart_self()

            if sync_url and now_ts - last_fetch >= fetch_interval:
                last_fetch = now_ts
                remote = fetch_remote_config(sync_url)
                if remote:
                    # 관리자가 퇴실 시각을 새로 저장했다면 오늘치를 다시 준비한다.
                    # 이걸 안 하면 "오늘 이미 띄웠음" 표시 때문에, 시각을 바꿔 저장해도
                    # 그날은 아무리 기다려도 안 뜬다 (관리자 입장에선 고장으로 보인다).
                    new_times = [e["time"] for e in schedule_of(remote)]
                    if new_times and new_times != [e["time"] for e in schedule_of(state)]:
                        if done_today:
                            log(
                                f"[QRcode] 퇴실 시각이 {', '.join(new_times)}(으)로 변경되어 "
                                "오늘 표시를 다시 준비합니다."
                            )
                        done_today = set()
                        state["done_times"] = []  # 아래 save_cache에서 함께 저장된다
                    if not synced_once:
                        synced_once = True
                        log(f"[QRcode] 중앙 설정 첫 동기화 성공 (시각 {remote.get('checkout_time')})")
                    if remote.get("checkout_time") != state.get("checkout_time") or remote.get(
                        "qr_image"
                    ) != state.get("qr_image") or remote.get("checkout_url") != state.get(
                        "checkout_url"
                    ) or remote.get("active_days") != state.get(
                        "active_days"
                    ) or remote.get("after_close_url") != state.get("after_close_url"):
                        log(
                            "[QRcode] 설정 갱신됨 -> 시각: "
                            f"{', '.join(e['time'] for e in schedule_of(remote)) or '없음'}, "
                            f"요일: {remote.get('active_days', state['active_days'])}"
                        )
                    state["checkout_url"] = remote.get("checkout_url", state.get("checkout_url", ""))
                    state["qr_image"] = remote.get("qr_image", state.get("qr_image"))
                    state["checkout_time"] = remote.get("checkout_time", state["checkout_time"])
                    state["checkout_times"] = remote.get("checkout_times", state.get("checkout_times"))
                    state["schedule"] = remote.get("schedule", state.get("schedule"))
                    state["intro_enabled"] = remote.get("intro_enabled", state.get("intro_enabled", True))
                    state["base_qr_image"] = remote.get("base_qr_image", state.get("base_qr_image"))
                    state["active_days"] = remote.get("active_days", state["active_days"])
                    state["after_close_url"] = remote.get("after_close_url", state.get("after_close_url"))
                    state["last_sync_ok"] = datetime.now().isoformat(timespec="seconds")
                    save_cache(state)

            # 설정 받기 결과까지 담아 보고한다 (켜질 때 + 6시간마다)
            if status_url and now_ts - last_status >= status_interval:
                last_status = now_ts
                if not post_status(status_url, build_status(state)):
                    # 실패하면 6시간을 기다리지 않고 3~7분 뒤 다시 보낸다. 시점을 섞어야
                    # 함께 실패한 PC들이 같은 순간에 다시 몰려 또 충돌하지 않는다.
                    last_status = now_ts - status_interval + random.randint(180, 420)

            now = datetime.now()
            now_hm = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")

            today_key = WEEKDAY_KEYS[now.weekday()]
            # 설정이 비어 있거나 null로 오면 기본값(월~금)을 쓴다.
            # 그냥 두면 None으로 비교하다 프로그램이 죽는다.
            active_days = state.get("active_days") or DEFAULT_ACTIVE_DAYS
            is_active_day = today_key in active_days

            if triggered_date != today:  # 날짜가 바뀌면 오늘치를 새로 시작
                triggered_date = today
                done_today = set()

            if is_active_day:
                entries = [e for e in schedule_of(state) if entry_applies_here(e)]
                times = [e["time"] for e in entries]
                target = due_time(now, times, done_today, catchup_minutes)
                if target:
                    # 한꺼번에 여러 시각을 놓쳤어도 창은 하나만 띄우고, 지나간 것들은
                    # 오늘치를 쓴 것으로 처리한다 (창이 연달아 여러 개 뜨지 않도록).
                    done_today.update(t for t in times if parse_hhmm(t) <= parse_hhmm(target))
                    entry = next(e for e in entries if e["time"] == target)
                    label = "공지" if entry.get("kind") == "notice" else "QR"
                    if entry.get("target_pc"):
                        label += f" (지정: {entry['target_pc']})"
                    intro = intro_for_entry(entry, state)
                    if intro:
                        label += " + 영상"
                    log(f"[QRcode] {now_hm} (설정 {target}) - {label} 화면 표시")
                    # 관리자 현황 화면에서 "이 PC가 실제로 QR을 봤는지"를 보기 위해 남긴다
                    state["last_shown"] = f"{today} {now_hm}"
                    state["done_date"] = today
                    state["done_times"] = sorted(done_today)
                    save_cache(state)
                    # 그 시각에 등록된 QR/링크로 띄운다 (시각마다 다를 수 있다)
                    show_qr_window(
                        entry, window_title, display_seconds, close_lock_seconds, intro, lock_after_intro
                    )

        except Exception:
            import traceback

            log("[QRcode] 처리 중 오류 (계속 동작합니다):\n" + traceback.format_exc())

        time.sleep(15)


def main():
    parser = argparse.ArgumentParser(description="퇴실 QR코드 자동 표시 프로그램")
    parser.add_argument("--test-now", action="store_true", help="스케줄 무시하고 즉시 QR 표시")
    parser.add_argument("--version", action="store_true", help="버전 출력 후 종료")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="네트워크/화면 없이 기본 동작만 점검하고 종료 (자동 업데이트 검증용)",
    )
    args = parser.parse_args()

    if args.version:
        print(VERSION)
        return

    if args.selftest:
        # 자동 업데이트가 이 코드를 적용해도 되는지 판단하는 기준이다.
        # 네트워크도 화면도 쓰지 않고, 핵심 경로만 실제로 한 번씩 실행해본다.
        config = load_config()
        assert parse_hhmm("18:00") == 1080, "parse_hhmm 오류"
        assert parse_hhmm("이상한값") is None, "parse_hhmm 예외 처리 오류"
        assert should_trigger(datetime(2026, 1, 1, 18, 0), "18:00", 0), "정시 트리거 오류"
        assert should_trigger(datetime(2026, 1, 1, 18, 30), "18:00", 120), "따라잡기 오류"
        assert not should_trigger(datetime(2026, 1, 1, 17, 59), "18:00", 120), "이른 트리거 오류"
        assert normalize_times("12:00, 18:00") == ["12:00", "18:00"], "시각 목록 파싱 오류"
        assert normalize_times(["18:00", "09:00"]) == ["09:00", "18:00"], "시각 목록 정렬 오류"
        assert normalize_times("이상한값") == [], "잘못된 시각 처리 오류"
        assert times_of({"checkout_time": "18:00"}) == ["18:00"], "구 설정 호환 오류"
        _t = ["09:00", "18:00"]
        assert due_time(datetime(2026, 1, 1, 18, 0), _t, set(), 120) == "18:00", "시각 선택 오류"
        assert due_time(datetime(2026, 1, 1, 9, 0), _t, set(), 120) == "09:00", "시각 선택 오류"
        assert due_time(datetime(2026, 1, 1, 18, 0), _t, {"18:00"}, 120) is None, "중복 표시 오류"
        _sched = schedule_of({
            "schedule": [
                {"time": "18:00", "qr_image": "B", "after_close_url": "b"},
                {"time": "12:00", "qr_image": "A", "after_close_url": "a"},
            ],
            "qr_image": "공용",
        })
        assert [e["time"] for e in _sched] == ["12:00", "18:00"], "일정표 정렬 오류"
        assert _sched[0]["qr_image"] == "A" and _sched[1]["qr_image"] == "B", "시각별 QR 오류"
        assert _sched[0]["after_close_url"] == "a", "시각별 링크 오류"
        _inherit = schedule_of({"schedule": [{"time": "09:00"}], "qr_image": "공용"})
        assert _inherit[0]["qr_image"] == "공용", "공용 이미지 상속 오류"
        assert _inherit[0]["message"] == "", "문구 기본값 오류"
        _msg = schedule_of({"schedule": [{"time": "09:00", "message": "특강"}], "qr_image": "공용"})
        assert _msg[0]["message"] == "특강" and _msg[0]["qr_image"] == "공용", "문구/상속 조합 오류"
        _base = schedule_of({"schedule": [{"time": "09:00"}], "base_qr_image": "기본", "qr_image": "구버전"})
        assert _base[0]["qr_image"] == "기본", "기본 QR 우선순위 오류"
        _notice = schedule_of({
            "schedule": [{"time": "20:00", "kind": "notice", "message": "공지"}],
            "base_qr_image": "기본",
        })
        assert _notice[0]["qr_image"] is None, "공지가 기본 QR을 물려받으면 안 됨"
        assert get_display_image(_notice[0]) is None, "문구 전용 공지는 이미지가 없어야 함"
        _notice_img = schedule_of({
            "schedule": [{"time": "20:00", "kind": "notice", "qr_image": "직접"}],
            "base_qr_image": "기본",
        })
        assert _notice_img[0]["qr_image"] == "직접", "공지 전용 이미지 오류"
        assert get_display_image({"checkout_url": "https://example.com"}) is not None, "퇴실 폴백 오류"
        _legacy = schedule_of({"checkout_times": ["09:00", "18:00"], "qr_image": "공용"})
        assert len(_legacy) == 2 and _legacy[1]["qr_image"] == "공용", "구 형식 호환 오류"
        _old_url = "https://gist.githubusercontent.com/sungho19141935-cyber/abc/raw/x.json"
        assert migrate_url(_old_url) == "https://gist.githubusercontent.com/hodumaru111/abc/raw/x.json", (
            "예전 계정 주소 변환 오류"
        )
        assert migrate_url("https://example.com/a/b") == "https://example.com/a/b", "무관한 주소 변환 오류"
        assert migrate_url(None) is None and migrate_url("") == "", "빈 주소 처리 오류"
        assert "sungho19141935-cyber" not in DEFAULT_UPDATE_URL, "기본 업데이트 주소가 예전 계정"
        _st = build_status({"schedule": [{"time": "18:00"}], "last_shown": "2026-01-01 18:00",
                            "last_sync_ok": "2026-01-01T17:00:00"})
        assert _st["app"] == "qrcode-checkout" and _st["version"] == VERSION, "상태 보고 기본값 오류"
        assert _st["times"] == "18:00" and _st["sync_ok"].startswith("2026"), "상태 보고 내용 오류"
        assert set(_st) == {"app", "pc", "version", "last_shown", "times", "sync_ok", "sync_error"}, (
            "상태 보고에 정해진 항목 외의 값이 들어감"
        )
        assert re.search(r"\(설정 (\d{2}:\d{2})\)", "2026-01-01 17:50:03 [QRcode] 17:50 (설정 17:50) - QR 화면 표시").group(1) == "17:50", (
            "로그 복원 패턴 오류"
        )
        assert intro_frame_index(0.0, 12, 60) == 0, "영상 첫 프레임 오류"
        assert intro_frame_index(2.5, 12, 60) == 30, "영상 중간 프레임 오류"
        assert intro_frame_index(5.0, 12, 60) is None, "영상 종료 판정 오류"
        assert intro_frame_index(1.0, 0, 60) is None, "fps 0 처리 오류"
        assert intro_for_entry({"kind": "notice"}, {}) is None, "공지에는 영상을 틀면 안 됨"
        assert load_intro({"intro_enabled": False}) is None, "스위치를 끄면 영상이 없어야 함"
        assert verify_asset(b"", "0" * 64) is not None, "빈 파일 거부 오류"
        assert verify_asset(b"x" * 10, "0" * 64) is not None, "체크섬 불일치 거부 오류"
        try:
            from PIL import features as _features

            _webp_ok = _features.check("webp")
        except Exception:
            _webp_ok = False
        if _webp_ok:  # WebP를 못 읽는 환경에서는 영상이 안 나올 뿐, 업데이트를 막지 않는다
            _buf = io.BytesIO()
            _f1, _f2 = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")
            _f1.save(_buf, format="WEBP", save_all=True, append_images=[_f2], duration=80)
            _good = _buf.getvalue()
            assert verify_asset(_good, hashlib.sha256(_good).hexdigest()) is None, "정상 영상 거부 오류"
            assert verify_asset(_good, "0" * 64) is not None, "변조 영상 통과 오류"
            _one = io.BytesIO()
            _f1.save(_one, format="WEBP")
            assert verify_asset(_one.getvalue(), hashlib.sha256(_one.getvalue()).hexdigest()) is not None, (
                "단일 프레임 거부 오류"
            )
        _here = current_pc_name()
        assert entry_applies_here({"target_pc": ""}), "빈 대상 PC는 전원 적용 오류"
        assert entry_applies_here({"target_pc": _here.upper()}), "대상 PC 대소문자 비교 오류"
        assert not entry_applies_here({"target_pc": _here + "-NOT-THIS-ONE"}), "다른 PC 차단 오류"
        _target_notice = schedule_of({
            "schedule": [{"time": "20:00", "kind": "notice", "message": "x", "target_pc": "OTHER"}]
        })
        assert _target_notice[0]["target_pc"] == "OTHER", "공지 대상 PC 보존 오류"
        _target_checkout = schedule_of({
            "schedule": [{"time": "18:00", "kind": "checkout", "target_pc": "OTHER"}],
            "base_qr_image": "기본",
        })
        assert _target_checkout[0]["target_pc"] == "", "퇴실 항목은 대상 PC를 무시해야 함"
        make_qr_image("https://example.com/selftest")  # 이미지 생성 경로
        int(config.get("display_seconds", 600))
        print(f"selftest OK (v{VERSION})")
        return

    config = load_config()

    if args.test_now:
        state = load_cache()
        state.setdefault("checkout_url", config.get("checkout_url", ""))
        state.setdefault("qr_image", config.get("qr_image"))
        state.setdefault("after_close_url", config.get("after_close_url"))
        if config.get("sync_url"):
            remote = fetch_remote_config(migrate_url(config["sync_url"]))
            if remote:
                state["checkout_url"] = remote.get("checkout_url", state.get("checkout_url", ""))
                state["qr_image"] = remote.get("qr_image", state.get("qr_image"))
                state["after_close_url"] = remote.get("after_close_url", state.get("after_close_url"))
                state["schedule"] = remote.get("schedule", state.get("schedule"))
                state["intro_enabled"] = remote.get("intro_enabled", state.get("intro_enabled", True))
        # 일정표가 있으면 가장 이른 시각의 설정으로 미리보기 한다
        entries = [e for e in schedule_of(state) if entry_applies_here(e)]
        first = entries[0] if entries else state
        show_qr_window(
            first,
            config.get("window_title", "퇴실 QR코드"),
            int(config.get("display_seconds", 600)),
            max(0, int(config.get("close_lock_seconds", DEFAULT_CLOSE_LOCK_SECONDS))),
            intro_for_entry(first, state) if entries else None,
            max(0, int(config.get("close_lock_after_intro_seconds", DEFAULT_LOCK_AFTER_INTRO_SECONDS))),
        )
        return

    try:
        run_scheduler(config)
    except KeyboardInterrupt:
        log("[QRcode] 종료합니다.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        # pythonw.exe에는 콘솔이 없어, 여기서 기록하지 않으면 프로그램이 왜 사라졌는지
        # 알 방법이 전혀 없다.
        import traceback

        log("[QRcode] 치명적 오류로 종료됩니다:\n" + traceback.format_exc())
        raise
