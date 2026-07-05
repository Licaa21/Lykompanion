"""Standalone OCR/visual-diff calibration tool - not part of the running app.

Captures the foreground monitor on a configurable interval, runs it through the same
capture -> resize -> OCR pipeline the game-state poller uses, and prints the full recognized
text plus the OCR-similarity ratio and visual-diff percent versus the previous capture - so you
can watch real numbers move while playing and pick sane values for
GAME_STATE_OCR_SIMILARITY_THRESHOLD / GAME_STATE_VISUAL_DIFF_THRESHOLD_PERCENT /
GAME_STATE_VISUAL_DIFF_NOISE_FLOOR_PERCENT before touching Settings.

Usage:
    .venv\\Scripts\\python.exe tools\\ocr_debug.py [interval_seconds]

Windows only; requires the same OCR-capable language pack as the real poller (see README).
"""

import asyncio
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops, ImageStat  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.ocr import windows_ocr  # noqa: E402
from app.services.screenshot.wgc_capture import capture_monitor_frame  # noqa: E402


def _visual_diff_percent(a: Image.Image, b: Image.Image) -> float:
    size = (settings.game_state_visual_diff_thumbnail_size, settings.game_state_visual_diff_thumbnail_size)
    a_thumb = a.convert("L").resize(size)
    b_thumb = b.convert("L").resize(size)
    diff = ImageChops.difference(a_thumb, b_thumb)
    return (ImageStat.Stat(diff).mean[0] / 255) * 100


async def main() -> None:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    ocr_max_width = settings.game_state_ocr_max_width
    print(
        f"OCR debug tool - capturing every {interval}s (GAME_STATE_OCR_MAX_WIDTH={ocr_max_width}, "
        f"GAME_STATE_VISUAL_DIFF_THUMBNAIL_SIZE={settings.game_state_visual_diff_thumbnail_size}). "
        "Ctrl+C to stop.\n"
    )

    last_image: Image.Image | None = None
    last_text: str | None = None

    while True:
        image = await capture_monitor_frame()
        if image is None:
            print(f"[{time.strftime('%H:%M:%S')}] capture returned no frame\n")
        else:
            if image.width > ocr_max_width:
                ratio = ocr_max_width / image.width
                image = image.resize((ocr_max_width, int(image.height * ratio)))
            text = (await windows_ocr.extract_text(image) or "").strip()
            normalized = " ".join(text.split())

            similarity = SequenceMatcher(None, normalized, last_text or "").ratio() if last_text is not None else None
            diff_percent = _visual_diff_percent(last_image, image) if last_image is not None else None

            print(
                f"[{time.strftime('%H:%M:%S')}] chars={len(text)} "
                f"ocr_similarity={'n/a' if similarity is None else f'{similarity:.3f}'} "
                f"visual_diff={'n/a' if diff_percent is None else f'{diff_percent:.1f}%'}"
            )
            print("-" * 60)
            print(text if text else "(no text detected)")
            print("-" * 60 + "\n")

            last_image = image
            last_text = normalized

        await asyncio.sleep(interval)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Windows only.")
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
