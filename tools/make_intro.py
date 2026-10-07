"""퇴실 인트로 영상(assets/intro.webp)을 화면녹화 mp4에서 만든다.

학생 PC에는 필요 없는 개발용 도구다 (requirements.txt에 넣지 않는다):

    pip install imageio-ffmpeg
    python tools/make_intro.py "원본.mp4"
    python tools/make_intro.py "원본.mp4" --captions "첫 자막" "둘째 자막" "셋째 자막"

만들어지는 것:
    assets/intro.webp   애니메이션 WebP (무음). 학생 프로그램이 Pillow로 프레임을 읽어 재생한다
    assets/intro.json   {"fps": 12}  프레임 간격. Pillow가 읽을 때 duration을 0으로 돌려주는
                        버전이 있어서, 재생 속도는 파일이 아니라 이 값을 믿는다

만든 뒤에는 VERSION을 올리고 `python release.py`로 version.json을 다시 만들어 함께 커밋한다.
원본 mp4는 저장소에 올리지 않는다.

아래 자르기 비율은 "인스타그램 릴스를 폰으로 화면녹화한 세로 영상" 기준으로 맞춘 값이다.
다른 영상이면 --crop-* 옵션이나 상수를 조정한다.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = r"C:\Windows\Fonts\malgunbd.ttf"

DEFAULT_CAPTIONS = ["집 갈 때가 됐잖아~", "QR 찍어야 하잖아~", "데일리퀘스트도 해야 돼~"]

# 화면녹화에 같이 찍힌 폰 UI를 잘라낸다 (원본 높이/너비에 대한 비율)
CROP_TOP, CROP_HEIGHT, CROP_WIDTH = 0.11, 0.66, 0.87
# 영상에 박혀 있는 원래 자막이 있던 띠 (원본 높이 비율). 새 자막 띠로 덮는다
ORIG_CAPTION_TOP, ORIG_CAPTION_BOTTOM = 0.26, 0.36
ORIG_WIDTH, ORIG_HEIGHT = 884, 1920  # 원본 해상도 기준으로 비율을 픽셀로 바꾼다


def find_ffmpeg():
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg를 찾지 못했습니다. `pip install imageio-ffmpeg` 후 다시 실행하세요.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("src", help="원본 mp4 경로")
    ap.add_argument("--start", type=float, default=0.5, help="자르기 시작 초 (기본 0.5)")
    ap.add_argument("--length", type=float, default=5.0, help="길이 초 (기본 5)")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--width", type=int, default=360, help="결과 가로 px (기본 360)")
    ap.add_argument("--quality", type=int, default=65, help="WebP 품질 (기본 65)")
    ap.add_argument("--captions", nargs="+", default=DEFAULT_CAPTIONS, help="자막 (길이를 균등 분할)")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets"), help="출력 폴더")
    ap.add_argument("--sheet", help="대표 프레임 이어붙인 미리보기 PNG 경로 (선택)")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    work = tempfile.mkdtemp(prefix="make_intro_")
    try:
        vf = (
            f"fps={args.fps},"
            f"crop=w=floor(iw*{CROP_WIDTH}/2)*2:h=floor(ih*{CROP_HEIGHT}/2)*2:x=0:y=floor(ih*{CROP_TOP}),"
            f"scale={args.width}:-1:flags=lanczos"
        )
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(args.start), "-t", str(args.length),
             "-i", args.src, "-vf", vf, os.path.join(work, "f_%03d.png")],
            check=True,
        )
        paths = sorted(glob.glob(os.path.join(work, "f_*.png")))
        if not paths:
            sys.exit("프레임을 하나도 뽑지 못했습니다. 경로와 시작 시각을 확인하세요.")
        W, H = Image.open(paths[0]).size

        scale = W / (ORIG_WIDTH * CROP_WIDTH)
        band_top = int((ORIG_CAPTION_TOP - CROP_TOP) * ORIG_HEIGHT * scale) - 4
        band_bot = int((ORIG_CAPTION_BOTTOM - CROP_TOP) * ORIG_HEIGHT * scale) + 4
        font = ImageFont.truetype(FONT, 26)

        frames = []
        per = args.length / len(args.captions)
        for i, pth in enumerate(paths):
            im = Image.open(pth).convert("RGBA")
            text = args.captions[min(len(args.captions) - 1, int((i / args.fps) / per))]
            # 불투명 띠: 반투명이면 밑에 있는 원래 자막이 비쳐 보인다. 양끝은 화면 밖까지.
            over = Image.new("RGBA", im.size, (0, 0, 0, 0))
            ImageDraw.Draw(over).rounded_rectangle(
                [-20, band_top, W + 20, band_bot], radius=14, fill=(20, 12, 40, 255)
            )
            im = Image.alpha_composite(im, over)
            d = ImageDraw.Draw(im)
            tw = d.textlength(text, font=font)
            d.text(((W - tw) / 2, (band_top + band_bot) / 2 - 17), text, font=font,
                   fill=(255, 235, 59, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
            frames.append(im.convert("RGB"))

        os.makedirs(args.out, exist_ok=True)
        webp = os.path.join(args.out, "intro.webp")
        frames[0].save(webp, save_all=True, append_images=frames[1:],
                       duration=int(1000 / args.fps), loop=0, quality=args.quality, method=4)
        with open(os.path.join(args.out, "intro.json"), "w", encoding="utf-8") as f:
            json.dump({"fps": args.fps}, f)

        if args.sheet:
            picks = [int(len(frames) * x) for x in (0.12, 0.5, 0.88)]
            sheet = Image.new("RGB", (W * 3 + 20, H), "white")
            for k, idx in enumerate(picks):
                sheet.paste(frames[idx], (k * (W + 10), 0))
            sheet.save(args.sheet)

        size = os.path.getsize(webp)
        print(f"프레임 {len(frames)}장, {W}x{H}, {size / 1e6:.2f} MB -> {webp}")
        if size > 3_000_000:
            print("경고: 3MB를 넘습니다. 학생 프로그램이 받지 않습니다 (--quality를 낮추세요).")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
