"""Cross-platform Chinese font lookup.

The original `model/eye_tracker/sternberg_task.py` and `itracker.py` hard-coded
``C:\\Windows\\Fonts\\msyh.ttc`` (Microsoft YaHei). On macOS / Linux this path
does not exist, so the modules crashed at startup.

This module probes a list of Chinese-capable font families for each platform
and returns the absolute path of the first one that exists. Result is cached
for the lifetime of the process.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Per-platform candidate font families, ordered by preference.
# matplotlib's font_manager indexes fonts shipped by the OS, so this works
# without requiring users to install anything extra.
_CANDIDATES_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "darwin": (
        "PingFang SC",
        "Hiragino Sans GB",
        "STHeiti",
        "Heiti SC",
        "STSong",
    ),
    "win32": (
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "Microsoft JhengHei",
    ),
    "linux": (
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei",
        "Source Han Sans CN",
    ),
}

# Direct file fallbacks if matplotlib's font_manager doesn't find anything.
_DIRECT_PATH_FALLBACKS: tuple[Path, ...] = (
    # macOS
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/Library/Fonts/Songti.ttc"),
    # Windows (kept here for completeness even though we won't run this branch on mac)
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
    # Linux
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
)


def _platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


@lru_cache(maxsize=1)
def get_chinese_font_path() -> Optional[str]:
    """Locate a Chinese-capable font file on this machine.

    Returns the absolute path as a string, or ``None`` if no font could be
    found. Callers should pass the result to ``pygame.font.Font(path, size)``;
    on a ``None`` result they should fall back to ``pygame.font.SysFont``.
    """
    # First try matplotlib's index — it knows about user-installed fonts too.
    try:
        from matplotlib import font_manager
    except ImportError:
        font_manager = None  # type: ignore[assignment]

    if font_manager is not None:
        for family in _CANDIDATES_BY_PLATFORM[_platform_key()]:
            try:
                path = font_manager.findfont(
                    font_manager.FontProperties(family=family),
                    fallback_to_default=False,
                )
                if path and Path(path).is_file():
                    return path
            except Exception:
                continue

    # Direct path fallback
    for p in _DIRECT_PATH_FALLBACKS:
        if p.is_file():
            return str(p)

    return None
