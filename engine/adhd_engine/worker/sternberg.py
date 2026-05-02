"""Sternberg visual-spatial working memory task.

Migrated from ``model/eye_tracker/sternberg_task.py`` for the engine refactor.

Migration notes (see plan §1.5, §1.6):

* **Plan §1.5.2** — ``_load_and_prepare_image`` now letterboxes images to
  preserve their aspect ratio (was stretching to a fixed 1.35:1 box, which
  squashed portrait photos horizontally).
* **Plan §1.5.3** — ``_normalize_brightness`` now uses ITU-R BT.601 perceptual
  luminance (0.299R + 0.587G + 0.114B) and an additive offset instead of a
  multiplicative gain, which prevents highlight clipping.
* **Plan §1.5.4** — ``_draw_distractor`` fills the background with
  :data:`adhd_engine.config.DISTRACTOR_BG_GRAY` instead of pure black, so the
  blank/shape/image distractor types don't differ in full-screen luminance by
  12× — which would otherwise create a strong pupil artefact across distractor
  conditions.
* **Plan §1.6** — removed Windows-only ``ctypes.windll.user32`` calls, replaced
  ``C:\\Windows\\Fonts\\msyh.ttc`` with cross-platform font lookup.
* Added ``ipc_publisher`` callable hook + ``session_id`` so the runner can
  forward trial events (``trial_start``, ``trial_end``, ``block_end``) to
  parent process.
"""

import os
import csv
import sys
import glob
import math
import random
import threading
import time
import numpy as np
import pygame
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from PIL import Image

from adhd_engine import config as engine_config
from adhd_engine.worker.fonts import get_chinese_font_path

_IS_WIN32 = sys.platform.startswith("win")

# ========== 任务配置 ==========
TASK_CONFIG = {
    'n_blocks': 8,
    'trials_per_block': 20,
    'fixation_ms': 500,
    'encoding_ms': 750,
    'encoding_gap_ms': 500,
    'distractor_ms': 500,
    'probe_ms': 1500,
    'feedback_ms': 500,
    'grid_size': 4,
    'dot_radius_frac': 0.015,
    'target_fps': 30,
}

# 干扰类型编码 (匹配 Pupil_dataset 的 distractor 字段)
DIST_BLANK = 3
DIST_NEUTRAL = 4
DIST_EMOTIONAL = 5
DIST_TASK_RELATED = 6

# 字符串 → 数值映射
DIST_NAME_TO_CODE = {
    "blank": DIST_BLANK,
    "neutral_image": DIST_NEUTRAL,
    "emotional_image": DIST_EMOTIONAL,
    "shape": DIST_TASK_RELATED,
}
DIST_CODE_TO_NAME = {v: k for k, v in DIST_NAME_TO_CODE.items()}

# Stimuli directory — sourced from the vendored model/ tree
DEFAULT_STIMULI_DIR = str(engine_config.STIMULI_DIR)


# ========== 图片加载与管理模块 ==========

def load_images(stimuli_dir: str = None) -> dict:
    """
    从 stimuli/neutral/ 和 stimuli/emotional/ 加载所有图片路径。

    Returns:
        {'neutral': [path, ...], 'emotional': [path, ...]}
    """
    if stimuli_dir is None:
        stimuli_dir = DEFAULT_STIMULI_DIR

    result = {'neutral': [], 'emotional': []}
    for category in ('neutral', 'emotional'):
        folder = os.path.join(stimuli_dir, category)
        if not os.path.isdir(folder):
            print(f"[警告] 图片文件夹不存在: {folder}")
            continue
        for ext in ('*.jpg', '*.jpeg', '*.png'):
            result[category].extend(
                sorted(glob.glob(os.path.join(folder, ext))))

    print(f"  图片加载: neutral={len(result['neutral'])}, "
          f"emotional={len(result['emotional'])}")
    return result


def _normalize_brightness(surface: pygame.Surface,
                          target_mean: float = 128.0) -> pygame.Surface:
    """Equalise the perceptual luminance of a pygame Surface.

    Plan §1.5.3 — uses ITU-R BT.601 luma (0.299R + 0.587G + 0.114B) and an
    additive offset rather than a multiplicative gain. The additive form does
    not blow out highlights to 255, and the perceptual weighting prevents
    bias against green-dominant images (forests, plants, etc.) that the old
    arithmetic-mean version under-compensated.
    """
    arr = pygame.surfarray.pixels3d(surface).copy()  # (W, H, 3)
    luma = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    current = float(luma.mean())
    if current < 1:
        return surface
    delta = target_mean - current
    arr = np.clip(arr.astype(np.float32) + delta, 0, 255).astype(np.uint8)
    return pygame.surfarray.make_surface(arr)


def _load_and_prepare_image(path: str, target_size: Tuple[int, int],
                            normalize: bool = True) -> pygame.Surface:
    """Load an image, letterbox it into ``target_size``, optionally normalise.

    Plan §1.5.2 — the original implementation called ``Image.resize`` directly
    which **stretches** images to fit. Portrait photos (face/laugh/book/etc.)
    were thus squashed horizontally to half their natural aspect ratio. We now
    use ``thumbnail`` to scale-and-fit, then paste over a 128-grey background.
    The 128 grey matches the brightness normalisation target so the surrounding
    fill blends with the equalised image content.
    """
    img = Image.open(path).convert('RGB')
    img.thumbnail(target_size, Image.LANCZOS)

    bg = Image.new('RGB', target_size, (128, 128, 128))
    px = (target_size[0] - img.width) // 2
    py = (target_size[1] - img.height) // 2
    bg.paste(img, (px, py))

    surface = pygame.image.fromstring(bg.tobytes(), bg.size, bg.mode)
    if normalize:
        surface = _normalize_brightness(surface)
    return surface


def generate_distractor_sequence(n_trials: int,
                                 types: list = None,
                                 n_blocks: int = 8) -> List[str]:
    """
    生成受控随机的干扰类型序列，保证：
    1. 全局各类型出现次数均衡
    2. 每 block 内基本均衡
    3. 不允许连续 3 次 emotional_image
    4. 尽量避免同一类型连续超过 2 次

    Returns: 长度为 n_trials 的字符串列表
    """
    if types is None:
        types = ["blank", "neutral_image", "emotional_image", "shape"]

    tpb = n_trials // n_blocks
    sequence = []

    for _ in range(n_blocks):
        # 每 block 内均衡分配
        n_per_type = tpb // len(types)
        remainder = tpb - n_per_type * len(types)
        block_seq = []
        for t in types:
            block_seq.extend([t] * n_per_type)
        # 余数随机分配
        extras = random.sample(types, remainder) if remainder > 0 else []
        block_seq.extend(extras)

        # 约束 shuffle: 不允许连续 3 次同类型，
        # 且与上一 block 末尾衔接时也不违反约束
        for _attempt in range(500):
            random.shuffle(block_seq)
            if not _check_sequence_constraints(block_seq):
                continue
            # 检查跨 block 衔接
            if sequence:
                tail = sequence[-2:]
                combined = tail + block_seq[:2]
                if not _check_sequence_constraints(combined):
                    continue
            break

        sequence.extend(block_seq)

    return sequence


def _check_sequence_constraints(seq: list) -> bool:
    """检查序列是否满足约束条件。"""
    for i in range(len(seq) - 2):
        if seq[i] == seq[i + 1] == seq[i + 2]:
            # 不允许任何类型连续 3 次
            return False
        if seq[i] == seq[i + 1] == "emotional_image":
            # 情绪图片连续 2 次也尽量避免（但不硬性禁止）
            pass
    return True


def get_distractor_stim(dtype: str,
                        image_cache: dict,
                        pool_indices: dict,
                        target_size: Tuple[int, int],
                        ) -> Tuple[Optional[pygame.Surface], str]:
    """Pick the next pre-rendered distractor surface from the cache.

    The cache is built once in :meth:`SternbergTask._preload_distractor_images`
    so we don't pay disk IO + decode + resize + brightness normalisation cost
    on every trial. The previous version did all of that per trial which made
    each trial start with a noticeable hitch on Windows CPU.

    Args:
        dtype: 'neutral_image' | 'emotional_image' | other
        image_cache: ``{'neutral': [(name, surface), ...], 'emotional': [...]}``
        pool_indices: cursor dict mutated in place
        target_size: kept for API compatibility but unused (surfaces are
            already at the right size)

    Returns:
        ``(surface, filename)`` or ``(None, "")`` if the cache is empty.
    """
    del target_size  # surfaces in the cache are already prepared

    category = None
    if dtype == "neutral_image":
        category = "neutral"
    elif dtype == "emotional_image":
        category = "emotional"
    else:
        return None, ""

    pool = image_cache.get(category, [])
    if not pool:
        return None, ""

    idx = pool_indices[category] % len(pool)
    pool_indices[category] = idx + 1
    name, surf = pool[idx]
    return surf, name


@dataclass
class TrialData:
    """单个 trial 的配置与结果。"""
    trial_num: int
    block_num: int
    load: int               # 1 or 2
    distractor_type: int     # 3-6 (数值编码, 供分类器使用)
    distractor_name: str     # "blank"/"neutral_image"/"emotional_image"/"shape"
    dot_positions: list      # 3 arrays of (row, col) tuples
    probe_pos: tuple         # (row, col)
    is_target: bool
    correct_answer: str      # 'f' or 'j'
    # --- 运行后填充 ---
    response: Optional[str] = None
    reaction_time: float = float('nan')
    correct: int = 0
    image_file: str = ""     # 干扰图片文件名 (若有)
    # --- 时间序列 ---
    pupil_series: list = field(default_factory=list)
    gaze_x_series: list = field(default_factory=list)
    gaze_y_series: list = field(default_factory=list)


class _CaptureWorker(threading.Thread):
    """Background capture + inference loop.

    Without this, ``_present_phase`` blocked every frame on
    ``cap.read()`` + ``FaceMesh.process`` + ``AFFNet.forward``, which adds
    up to 50-80 ms per frame on Windows CPU. That:

    1. Made the pygame draw loop run slower than 30 fps — phases ran long
       and felt choppy ("卡卡的").
    2. Pushed each visual flip behind the blocking capture, so when a user
       pressed SPACE to advance from the instructions screen the new trial
       phase didn't appear for 50-80 ms — the user perceived the old
       screen "overlapping" with the new task.

    Moving capture onto a daemon thread decouples rendering from inference.
    The main thread (30 fps) just reads the **latest** captured frame from a
    lock-protected slot — non-blocking. The capture thread runs as fast as
    the camera + model allow, independently of the paradigm's 30 fps clock.

    Thread-safety — OpenCV, MediaPipe, and PyTorch all release the GIL
    during their heavy C++/CUDA code, so this actually parallelises on
    CPython despite the GIL.
    """

    def __init__(self, gaze_system):
        super().__init__(name="adhd-capture-worker", daemon=True)
        self._gs = gaze_system
        self._lock = threading.Lock()
        # IMPORTANT: do NOT name this ``self._stop`` — threading.Thread
        # has an internal ``_stop()`` method that gets invoked during
        # thread shutdown (including the KeyboardInterrupt path). Naming
        # our Event ``_stop`` would shadow the method with an instance,
        # and the next time Python internals called ``self._stop()`` it
        # would crash with ``'Event' object is not callable``.
        self._stop_event = threading.Event()
        self._latest: Tuple[float, float, float] = (
            float("nan"), float("nan"), float("nan"),
        )

    def run(self) -> None:
        """Tight capture loop — camera + features + gaze prediction."""
        gs = self._gs
        while not self._stop_event.is_set():
            try:
                ok, frame = gs.cap.read()
            except Exception:
                time.sleep(0.01)
                continue
            if not ok:
                time.sleep(0.01)
                continue

            ff = gs._extract(frame)
            if ff is None:
                with self._lock:
                    self._latest = (float("nan"), float("nan"),
                                    float("nan"))
                continue

            gs.feat_buf.append(ff.feat)
            pupil = ff.pupil_proxy

            gx, gy = float("nan"), float("nan")
            if len(gs.feat_buf) >= 3:
                feat = gs._weighted_mean_feat()
                mx, my = gs._predict_screen_point(feat)
                sx, sy = gs.smoother.update(float(mx), float(my))
                gx = float(np.clip(sx, 0, gs.sw - 1))
                gy = float(np.clip(sy, 0, gs.sh - 1))

            with self._lock:
                self._latest = (gx, gy, pupil)

    def latest(self) -> Tuple[float, float, float]:
        """Non-blocking read of the most recent captured sample."""
        with self._lock:
            return self._latest

    def stop(self) -> None:
        """Signal the capture loop to exit. Safe to call multiple times."""
        self._stop_event.set()


class SternbergTask:
    """Sternberg visual-spatial working memory task.

    Wraps a calibrated :class:`adhd_engine.worker.gaze_system.GazeSystem`
    instance — re-uses the screen, camera, and gaze prediction pipeline.
    """

    def __init__(
        self,
        gaze_system,
        config=None,
        stimuli_dir=None,
        ipc_publisher: Optional[Callable] = None,
        session_id: Optional[str] = None,
    ):
        self.gs = gaze_system
        self.cfg = config or TASK_CONFIG
        self.screen = gaze_system.screen
        self.sw = gaze_system.sw
        self.sh = gaze_system.sh
        self.clock = pygame.time.Clock()
        self.ipc_publisher = ipc_publisher
        self.session_id = session_id

        # Grid geometry (centred square)
        cell = self.sh * 0.12
        total = cell * self.cfg['grid_size']
        self.cell_size = cell
        self.grid_x0 = (self.sw - total) / 2
        self.grid_y0 = (self.sh - total) / 2
        self.dot_r = max(8, int(self.sh * self.cfg['dot_radius_frac']))

        # Distractor image size — kept at the original ~1.35:1 frame so the
        # 4×4 encoding grid still spans more area; the letterboxing fix
        # (plan §1.5.2) preserves aspect ratio inside this frame.
        self.dist_img_size = (int(self.sh * 0.35), int(self.sh * 0.26))

        # Cross-platform Chinese font (plan §1.6)
        zh_font_path = get_chinese_font_path()
        if zh_font_path is not None:
            self.font_big = pygame.font.Font(zh_font_path, 56)
            self.font_med = pygame.font.Font(zh_font_path, 36)
            self.font_sm = pygame.font.Font(zh_font_path, 24)
        else:
            self.font_big = pygame.font.SysFont(
                'pingfangsc,microsoftyahei,simhei,simsun,notosanscjksc', 56)
            self.font_med = pygame.font.SysFont(
                'pingfangsc,microsoftyahei,simhei,simsun,notosanscjksc', 36)
            self.font_sm = pygame.font.SysFont(
                'pingfangsc,microsoftyahei,simhei,simsun,notosanscjksc', 24)

        # Load distractor images
        # Pre-load all distractor images at task init so per-trial latency
        # is zero. The previous version loaded each image from disk + resized
        # + normalised brightness on every trial — at 22 MB PNGs this caused
        # a noticeable hitch on every image-distractor trial.
        self.image_pools = load_images(stimuli_dir)
        self.image_cache: dict = self._preload_distractor_images()
        self.pool_indices = {'neutral': 0, 'emotional': 0}

        self.trials: List[TrialData] = []
        self.results: List[TrialData] = []

        # Background capture worker — started at the beginning of `run()`
        # and stopped in the finally clause so camera/inference runs in
        # parallel with the 30 fps draw loop.
        self._capture_worker: Optional[_CaptureWorker] = None

    def _preload_distractor_images(self) -> dict:
        """Decode + resize + normalise every distractor image once.

        Returns ``{'neutral': [(filename, Surface), ...], 'emotional': [...]}``,
        with each list shuffled. Subsequent trial dispatch picks surfaces from
        these lists with zero disk IO.
        """
        cache: dict = {'neutral': [], 'emotional': []}
        total_loaded = 0
        t0 = time.perf_counter()
        for category, paths in self.image_pools.items():
            for path in paths:
                try:
                    surf = _load_and_prepare_image(path, self.dist_img_size)
                    cache[category].append((os.path.basename(path), surf))
                    total_loaded += 1
                except Exception as exc:
                    print(f"[sternberg] failed to preload {path}: {exc}")
            random.shuffle(cache[category])
        elapsed = time.perf_counter() - t0
        print(f"[sternberg] preloaded {total_loaded} distractor images "
              f"in {elapsed:.2f}s "
              f"(neutral={len(cache['neutral'])}, "
              f"emotional={len(cache['emotional'])})")
        return cache

    def _emit(self, event_type: str, **payload) -> None:
        """Forward a task event to the parent process via the IPC hook."""
        if self.ipc_publisher is None:
            return
        try:
            self.ipc_publisher({
                "type": event_type,
                "session_id": self.session_id,
                **payload,
            })
        except Exception as exc:  # pragma: no cover
            print(f"[sternberg] event publisher raised: {exc}")

    # ──────────────────────────────────────
    # 网格与坐标工具
    # ──────────────────────────────────────

    def _all_grid_pos(self):
        g = self.cfg['grid_size']
        return [(r, c) for r in range(g) for c in range(g)]

    def _grid_to_px(self, row, col):
        x = int(self.grid_x0 + (col + 0.5) * self.cell_size)
        y = int(self.grid_y0 + (row + 0.5) * self.cell_size)
        return x, y

    # ──────────────────────────────────────
    # Trial 生成
    # ──────────────────────────────────────

    def generate_trials(self):
        """生成 160 个伪随机化试次，确保 block 内条件与干扰类型均衡。"""
        n_blocks = self.cfg['n_blocks']
        tpb = self.cfg['trials_per_block']
        n_total = n_blocks * tpb

        # 1. 生成受控随机干扰序列
        dist_types_str = ["blank", "neutral_image",
                          "emotional_image", "shape"]
        dist_sequence = generate_distractor_sequence(
            n_total, dist_types_str, n_blocks)

        # 2. 生成 load 序列（每 block 内 50% load1, 50% load2）
        load_sequence = []
        for _ in range(n_blocks):
            block_loads = [1] * (tpb // 2) + [2] * (tpb // 2)
            random.shuffle(block_loads)
            load_sequence.extend(block_loads)

        # 每 block 内 shuffle 后重新打包图片池索引
        self.pool_indices = {'neutral': 0, 'emotional': 0}
        for k in self.image_pools:
            random.shuffle(self.image_pools[k])

        trial_num = 0
        all_trials = []

        for block in range(n_blocks):
            block_start = block * tpb
            block_trials = []

            for i in range(tpb):
                trial_num += 1
                load = load_sequence[block_start + i]
                dist_name = dist_sequence[block_start + i]
                dist_code = DIST_NAME_TO_CODE[dist_name]

                # 在 4×4 网格中随机选取不重复的点
                all_pos = self._all_grid_pos()
                n_dots = load * 3
                selected = random.sample(all_pos, n_dots)
                arrays = [selected[j * load:(j + 1) * load]
                          for j in range(3)]

                # 50% target probe
                is_target = (i % 2 == 0)
                if is_target:
                    probe = random.choice(selected)
                else:
                    remaining = [p for p in all_pos if p not in selected]
                    probe = random.choice(remaining)

                block_trials.append(TrialData(
                    trial_num=trial_num,
                    block_num=block + 1,
                    load=load,
                    distractor_type=dist_code,
                    distractor_name=dist_name,
                    dot_positions=arrays,
                    probe_pos=probe,
                    is_target=is_target,
                    correct_answer='f' if is_target else 'j',
                ))

            # block 内已在 dist_sequence 中约束过，不再 shuffle
            all_trials.extend(block_trials)

        self.trials = all_trials
        return all_trials

    # ──────────────────────────────────────
    # 绘制函数
    # ──────────────────────────────────────

    def _draw_fixation(self):
        """Fixation cross. Background stays pure black for paradigm fidelity;
        the cross itself gets a warm off-white tint + faint grid backdrop
        so the trial phases don't look like a completely lifeless black
        void compared to the instructions screen."""
        self.screen.fill((0, 0, 0))
        self._draw_faint_grid()
        cx, cy = self.sw // 2, self.sh // 2
        sz = int(self.sh * 0.025)
        # Warm off-white — total luminance virtually identical to pure
        # white (well within the webcam-pupil noise floor) but feels less
        # clinical.
        cross_color = (255, 245, 230)
        pygame.draw.line(self.screen, cross_color,
                         (cx - sz, cy), (cx + sz, cy), 4)
        pygame.draw.line(self.screen, cross_color,
                         (cx, cy - sz), (cx, cy + sz), 4)

    def _draw_faint_grid(self):
        """Paint a barely-visible 4×4 grid guideline.

        Paradigm-safe polish: the grid lines are at (18, 18, 20), only 7% of
        full luminance, so they don't meaningfully change the full-screen
        light reaching the eye. This gives children a spatial reference for
        "where the dots can be" without distracting from the dots themselves.
        """
        g = self.cfg['grid_size']  # 4
        color = (18, 18, 20)
        # Vertical lines: g + 1 of them, at column boundaries
        for i in range(g + 1):
            x = int(self.grid_x0 + i * self.cell_size)
            pygame.draw.line(
                self.screen, color,
                (x, int(self.grid_y0)),
                (x, int(self.grid_y0 + g * self.cell_size)),
                1,
            )
        # Horizontal lines
        for i in range(g + 1):
            y = int(self.grid_y0 + i * self.cell_size)
            pygame.draw.line(
                self.screen, color,
                (int(self.grid_x0), y),
                (int(self.grid_x0 + g * self.cell_size), y),
                1,
            )

    def _draw_aa_circle(self, color, center, radius):
        """Anti-aliased filled circle using pygame.gfxdraw.

        Much smoother than ``pygame.draw.circle`` for small-to-medium
        circles. Used for the encoding dots and the probe so jagged edges
        don't distract children from the task.
        """
        import pygame.gfxdraw as gfxdraw
        x, y = int(center[0]), int(center[1])
        r = int(radius)
        gfxdraw.filled_circle(self.screen, x, y, r, color)
        gfxdraw.aacircle(self.screen, x, y, r, color)

    # ──────────────────────────────────────
    # Decorative pygame-drawn icons
    # ──────────────────────────────────────
    #
    # pygame's text rendering uses SDL_ttf which can only render glyphs
    # present in the loaded TrueType font. Our Chinese fonts (Hiragino
    # Sans GB on macOS, Microsoft YaHei on Windows) do NOT include emoji
    # codepoints (🎯 🌟 🏆 ✓ etc), so rendering them produces .notdef
    # rectangles ("□ with a diagonal inside"). Instead of shipping a
    # color-emoji font (large download + painful to get working in
    # SDL_ttf on all three OSes), we draw the icons we actually need with
    # pygame primitives. They look clean at any size and always render.

    def _draw_target_icon(self, center: Tuple[int, int], radius: int) -> None:
        """Concentric-circle target icon in the brand palette.

        Used in place of 🎯 in the instruction screen.
        """
        cx, cy = center
        # Outer ring —柚子橘
        self._draw_aa_circle((255, 140, 66), (cx, cy), radius)
        # Inner ring — white
        self._draw_aa_circle((255, 255, 255), (cx, cy), int(radius * 0.72))
        # Middle ring — 柚子橘
        self._draw_aa_circle((255, 140, 66), (cx, cy), int(radius * 0.45))
        # Bullseye — white
        self._draw_aa_circle((255, 255, 255), (cx, cy), int(radius * 0.18))

    def _draw_trophy_icon(self, center: Tuple[int, int], size: int) -> None:
        """A simple trophy shape (cup + base) in warm gold.

        Used in place of 🏆 on the completion screen.
        """
        cx, cy = center
        gold = (255, 191, 77)
        dark_gold = (200, 140, 40)

        # Cup body — rounded rectangle
        body_w = int(size * 0.62)
        body_h = int(size * 0.58)
        body_rect = pygame.Rect(
            cx - body_w // 2, cy - size // 2, body_w, body_h)
        pygame.draw.rect(self.screen, gold, body_rect,
                         border_radius=int(size * 0.12))

        # Rim line
        pygame.draw.rect(self.screen, dark_gold, body_rect,
                         width=max(3, size // 26),
                         border_radius=int(size * 0.12))

        # Handles — two small arcs on each side
        handle_w = int(size * 0.20)
        handle_h = int(size * 0.34)
        for sign in (-1, +1):
            hx = cx + sign * body_w // 2
            handle_rect = pygame.Rect(
                hx - handle_w // 2, cy - size // 2 + int(size * 0.08),
                handle_w, handle_h,
            )
            pygame.draw.arc(
                self.screen, gold, handle_rect,
                math.pi / 2, 3 * math.pi / 2,
                max(3, size // 22),
            )

        # Stem
        stem_w = int(size * 0.16)
        stem_h = int(size * 0.18)
        stem_rect = pygame.Rect(
            cx - stem_w // 2, cy + body_h // 2 - int(size * 0.04),
            stem_w, stem_h,
        )
        pygame.draw.rect(self.screen, dark_gold, stem_rect)

        # Base plate
        base_w = int(size * 0.72)
        base_h = int(size * 0.10)
        base_rect = pygame.Rect(
            cx - base_w // 2, cy + body_h // 2 + stem_h - int(size * 0.02),
            base_w, base_h,
        )
        pygame.draw.rect(self.screen, gold, base_rect,
                         border_radius=base_h // 2)

    def _draw_star_icon(self, center: Tuple[int, int], size: int,
                        color: Tuple[int, int, int]) -> None:
        """5-point filled star drawn as a polygon.

        Used in place of ⭐ / 🌟 wherever the kid-friendly screens want
        celebratory decoration.
        """
        cx, cy = center
        # 10 vertices alternating outer and inner radius
        outer = size / 2
        inner = outer * 0.45
        points = []
        for i in range(10):
            r = outer if i % 2 == 0 else inner
            angle = -math.pi / 2 + i * math.pi / 5
            points.append((
                cx + r * math.cos(angle),
                cy + r * math.sin(angle),
            ))
        pygame.draw.polygon(self.screen, color, points)
        # Thin darker outline for definition
        import pygame.gfxdraw as gfxdraw
        darker = tuple(max(0, c - 60) for c in color)
        gfxdraw.aapolygon(self.screen, points, darker)

    def _draw_dots(self, positions):
        self.screen.fill((0, 0, 0))
        self._draw_faint_grid()
        for r, c in positions:
            px, py = self._grid_to_px(r, c)
            self._draw_aa_circle((255, 255, 255), (px, py), self.dot_r)

    # ──────────────────────────────────────
    # Distractor resolution (per-trial) + rendering (per-frame)
    # ──────────────────────────────────────
    #
    # IMPORTANT — these MUST stay separate. The previous version had a single
    # ``_draw_distractor`` method that loaded the image / re-randomised the
    # shape positions inside the per-frame draw callback. Because
    # ``_present_phase`` calls the draw_fn ~15 times per 500 ms distractor at
    # 30 fps, that meant:
    #
    #   * image distractors flashed 15 different photos in 500 ms (cycling
    #     through ``pool_indices`` once per frame), instead of one photo for
    #     500 ms as the Rojas-Líbano paradigm requires
    #   * shape distractors re-randomised the dot positions every frame,
    #     producing a flickering kaleidoscope instead of a single static
    #     dot pattern
    #   * disk IO + image normalisation ran 15× per trial, causing frame
    #     drops on top of the visual mess
    #
    # The fix: resolve the distractor stimulus ONCE per trial in
    # ``_resolve_distractor`` and store it in a small dataclass payload. The
    # ``_render_distractor`` method is then a pure paint operation with zero
    # side effects, safe to invoke per-frame.

    def _resolve_distractor(self, trial: TrialData) -> dict:
        """Resolve a trial's distractor content exactly once.

        Mutates ``trial.image_file`` (image distractors only) and the shared
        ``self.pool_indices`` cursor. Subsequent per-frame draw calls receive
        the returned payload as immutable data.
        """
        dtype = trial.distractor_name

        if dtype in ("neutral_image", "emotional_image"):
            surf, img_name = get_distractor_stim(
                dtype, self.image_cache, self.pool_indices,
                self.dist_img_size)
            trial.image_file = img_name
            return {"type": "image", "surface": surf,
                    "fallback_label": dtype}

        if dtype == "shape":
            # Random dot pattern for the task-related distractor — resolved
            # ONCE so the user sees a stable pattern for 500 ms instead of
            # 15 different ones.
            n_dots = random.randint(4, 8)
            positions = [
                (random.randint(0, self.cfg['grid_size'] - 1),
                 random.randint(0, self.cfg['grid_size'] - 1))
                for _ in range(n_dots)
            ]
            return {"type": "shape", "positions": positions}

        # blank — no payload needed
        return {"type": "blank"}

    def _render_distractor(self, payload: dict) -> None:
        """Pure paint of a pre-resolved distractor payload.

        Plan §1.5.4 — fills with :data:`engine_config.DISTRACTOR_BG_GRAY` so
        the per-distractor full-screen luminance differs by far less than 12×
        across blank/shape/image types.
        """
        self.screen.fill(engine_config.DISTRACTOR_BG_GRAY)
        cx, cy = self.sw // 2, self.sh // 2
        ptype = payload.get("type", "blank")

        if ptype == "blank":
            return

        if ptype == "image":
            surf = payload.get("surface")
            if surf is not None:
                iw, ih = surf.get_size()
                self.screen.blit(surf, (cx - iw // 2, cy - ih // 2))
                return
            # Fallback rendering when the image library is empty
            w, h = self.dist_img_size
            label_kind = payload.get("fallback_label", "neutral_image")
            color = (120, 120, 120) if label_kind == "neutral_image" \
                else (180, 50, 50)
            label = "中性干扰(无图片)" if label_kind == "neutral_image" \
                else "情绪干扰(无图片)"
            pygame.draw.rect(self.screen, color,
                             (cx - w // 2, cy - h // 2, w, h))
            t = self.font_sm.render(label, True, (220, 220, 220))
            self.screen.blit(t, (cx - t.get_width() // 2,
                                 cy - t.get_height() // 2))
            return

        if ptype == "shape":
            for rr, cc in payload.get("positions", []):
                px, py = self._grid_to_px(rr, cc)
                pygame.draw.circle(self.screen, (100, 100, 100),
                                   (px, py), self.dot_r)

    def _draw_probe(self, pos):
        self.screen.fill((0, 0, 0))
        self._draw_faint_grid()
        px, py = self._grid_to_px(*pos)
        # AA yellow probe dot, slightly larger than the encoding dots so
        # the child knows "this is the one you answer about"
        self._draw_aa_circle((255, 220, 50), (px, py), self.dot_r + 3)
        hint = self.font_sm.render("1 = 出现过    2 = 没出现过（主键盘或小键盘）", True, (180, 180, 180))
        self.screen.blit(
            hint, (self.sw // 2 - hint.get_width() // 2, self.sh - 72))

    def _draw_feedback(self, is_correct):
        """Trial feedback screen — warm kid-friendly version.

        Paradigm constraints — this screen is 500 ms between trials, so the
        pupil doesn't have much time to recover before the next trial's
        fixation. We keep the **background at pure (0,0,0) black** so the
        pupil baseline for the next trial is unaffected. Only the colored
        badge + text changes.

        Design:
          * Big circular badge (mint for correct, soft orange for incorrect)
          * Simple Chinese word in white inside the badge
          * Encouraging sub-label below
        """
        self.screen.fill((0, 0, 0))
        self._draw_faint_grid()

        cx = self.sw // 2
        cy = self.sh // 2

        if is_correct:
            badge_color = (78, 205, 196)      # 薄荷青 (secondary)
            main_word = "太棒了"
            sub_word = "继续加油"
        else:
            badge_color = (255, 140, 66)      # 柚子橘 (NOT red — gentler)
            main_word = "没关系"
            sub_word = "下一题加油"

        # Circular badge — small enough that the full-screen luminance
        # delta stays below the webcam-pupil noise floor.
        badge_r = max(90, int(self.sh * 0.12))
        badge_center = (cx, cy - 40)
        self._draw_aa_circle(badge_color, badge_center, badge_r)

        # Draw checkmark (correct) or cross (incorrect) as pygame lines
        # so we don't depend on font glyphs for these symbols.
        bx, by = badge_center
        if is_correct:
            # Checkmark: three lines forming a ✓
            mark_w = max(6, badge_r // 7)
            p1 = (bx - badge_r // 2, by)
            p2 = (bx - badge_r // 10, by + badge_r // 3)
            p3 = (bx + badge_r // 2, by - badge_r // 3)
            pygame.draw.line(self.screen, (255, 255, 255), p1, p2, mark_w)
            pygame.draw.line(self.screen, (255, 255, 255), p2, p3, mark_w)
        else:
            # Arrow pointing right — "next attempt" feel
            mark_w = max(6, badge_r // 7)
            # Horizontal shaft
            pygame.draw.line(
                self.screen, (255, 255, 255),
                (bx - badge_r // 2, by),
                (bx + badge_r // 3, by),
                mark_w,
            )
            # Arrow head (two lines)
            pygame.draw.line(
                self.screen, (255, 255, 255),
                (bx + badge_r // 3, by),
                (bx, by - badge_r // 3),
                mark_w,
            )
            pygame.draw.line(
                self.screen, (255, 255, 255),
                (bx + badge_r // 3, by),
                (bx, by + badge_r // 3),
                mark_w,
            )

        # Main word below the badge, in warm white for readability
        main_surf = self.font_big.render(main_word, True, (255, 245, 230))
        self.screen.blit(
            main_surf,
            (cx - main_surf.get_width() // 2, cy + badge_r + 10),
        )

        # Small encouragement below — uses the badge color
        sub_surf = self.font_sm.render(sub_word, True, badge_color)
        self.screen.blit(
            sub_surf,
            (cx - sub_surf.get_width() // 2, cy + badge_r + 70),
        )

    # ──────────────────────────────────────
    # 帧级数据采集
    # ──────────────────────────────────────

    def _capture_one_frame(self):
        """Return the latest (gaze_x, gaze_y, pupil_proxy) sample.

        While a ``_CaptureWorker`` is running (during ``run()``), this is a
        non-blocking read from the worker's shared slot. Otherwise (e.g.
        during a standalone test) it falls back to a synchronous
        camera+inference call — slower but keeps the API compatible.
        """
        worker = self._capture_worker
        if worker is not None and worker.is_alive():
            return worker.latest()

        # Synchronous fallback — same code path the single-threaded
        # version used. Keeps tests that construct a SternbergTask without
        # calling run() working.
        ok, frame = self.gs.cap.read()
        if not ok:
            return np.nan, np.nan, np.nan

        ff = self.gs._extract(frame)
        if ff is None:
            return np.nan, np.nan, np.nan

        self.gs.feat_buf.append(ff.feat)
        pupil = ff.pupil_proxy

        gx, gy = np.nan, np.nan
        if len(self.gs.feat_buf) >= 3:
            feat = self.gs._weighted_mean_feat()
            mx, my = self.gs._predict_screen_point(feat)
            sx, sy = self.gs.smoother.update(float(mx), float(my))
            gx = float(np.clip(sx, 0, self.sw - 1))
            gy = float(np.clip(sy, 0, self.sh - 1))

        return gx, gy, pupil

    def _present_phase(self, draw_fn, duration_ms, trial=None, check_keys=False):
        """Show a stimulus phase for ``duration_ms`` while recording gaze.

        Loop ordering (critical for smooth transitions):

            events → **draw → flip → capture → sleep**

        The previous version did ``capture → draw → flip``, which blocked
        every iteration on ~50 ms of camera + MediaPipe + AFFNet before
        updating the screen. That produced two visible bugs:

        1. When SPACE advanced past the instructions screen, the first
           trial's fixation didn't appear for ~50 ms because the loop's
           first step was a blocking capture, not a draw. Users saw the
           cream instructions "overlapping" with the start of the trial.

        2. Phase durations ran long because each iteration exceeded the
           33 ms frame budget, so the loop completed its ``n_frames``
           iterations slower than the paradigm's 500 ms / 750 ms targets.

        Now we draw + flip first (instant visual update) and then read the
        latest gaze sample from the background ``_CaptureWorker`` — which
        is non-blocking, so each iteration fits comfortably in the 33 ms
        budget.
        """
        fps = self.cfg['target_fps']
        n_frames = max(1, int(duration_ms * fps / 1000))
        response = None
        rt = float('nan')
        t_start = time.perf_counter()

        for _ in range(n_frames):
            # 1. Event handling (non-blocking)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                    if check_keys and response is None:
                        if event.key == pygame.K_f:
                            response = 'f'
                            rt = time.perf_counter() - t_start
                        elif event.key == pygame.K_j:
                            response = 'j'
                            rt = time.perf_counter() - t_start

            # 2. Draw + flip FIRST — screen updates immediately, no
            #    perceived lag at phase transitions.
            draw_fn()
            pygame.display.flip()

            # 3. Gaze sample (non-blocking — from background worker).
            gx, gy, pupil = self._capture_one_frame()
            if trial is not None:
                trial.gaze_x_series.append(gx)
                trial.gaze_y_series.append(gy)
                trial.pupil_series.append(pupil)

            # 4. Maintain the 30 fps clock. If the loop body ran fast
            #    (which it now does, no blocking inference), tick sleeps.
            self.clock.tick(fps)

        return response, rt

    # ──────────────────────────────────────
    # 指导语与休息屏幕
    # ──────────────────────────────────────

    def _show_instructions(self):
        """Warm cream background + large rounded card with kid-friendly copy.

        Trial-phase backgrounds (fixation/encoding/probe/feedback) are kept
        at the paradigm-compliant pure black so pupil features are not
        perturbed — only the long-text screens (instructions / break / done)
        get the friendly warm palette.

        Note — no emoji characters are used in any ``render()`` call.
        The loaded Chinese fonts (Hiragino Sans GB, Microsoft YaHei) don't
        contain emoji glyphs, so any emoji renders as a .notdef rectangle.
        We use ``_draw_target_icon`` / ``_draw_star_icon`` instead.
        """
        self.screen.fill((255, 248, 240))

        cx = self.sw // 2
        cy = self.sh // 2

        # Target icon — drawn with pygame primitives (replaces 🎯)
        self._draw_target_icon((cx, cy - 260), 44)

        title = self.font_big.render("视觉记忆挑战", True, (43, 24, 16))
        self.screen.blit(title, (cx - title.get_width() // 2, cy - 190))

        subtitle = self.font_med.render(
            "用眼睛和手指一起闯关", True, (139, 111, 92))
        self.screen.blit(subtitle,
                         (cx - subtitle.get_width() // 2, cy - 120))

        # 4 step bullets with colored number badges
        steps = [
            ("1", (78, 205, 196),  "盯住中间的十字"),
            ("2", (255, 140, 66),  "记住圆点在哪里出现过 (一共 3 屏)"),
            ("3", (255, 209, 102), "看一眼干扰画面，不要分心"),
            ("4", (231, 76, 60),
             "看到圆点时：出现过按 1，没出现过按 2（主键盘或小键盘）"),
        ]
        y0 = cy - 40
        for i, (num, color, text) in enumerate(steps):
            yy = y0 + i * 58
            badge_x = cx - 280
            pygame.draw.circle(
                self.screen, color, (badge_x, yy + 18), 22)
            num_surf = self.font_med.render(num, True, (255, 255, 255))
            self.screen.blit(
                num_surf,
                (badge_x - num_surf.get_width() // 2,
                 yy + 18 - num_surf.get_height() // 2),
            )
            text_surf = self.font_med.render(text, True, (43, 24, 16))
            self.screen.blit(text_surf, (badge_x + 36, yy + 4))

        # Progress note (use ASCII 'x' instead of '×' in case a font
        # doesn't carry it — ASCII is safest)
        info_text = self.font_sm.render(
            f"共 {self.cfg['n_blocks']} 关 x "
            f"{self.cfg['trials_per_block']} 题  ·  约 25 分钟",
            True, (139, 111, 92))
        self.screen.blit(info_text,
                         (cx - info_text.get_width() // 2, cy + 240))

        # Big start button — no emoji, just Chinese text
        btn_text = self.font_big.render(
            "按任意键 开始闯关", True, (255, 255, 255))
        btn_w = btn_text.get_width() + 80
        btn_h = btn_text.get_height() + 36
        btn_rect = pygame.Rect(
            cx - btn_w // 2, cy + 290, btn_w, btn_h)
        pygame.draw.rect(
            self.screen, (0, 0, 0),
            btn_rect.move(0, 6),
            border_radius=btn_h // 2,
        )
        pygame.draw.rect(
            self.screen, (255, 140, 66),
            btn_rect,
            border_radius=btn_h // 2,
        )
        self.screen.blit(
            btn_text,
            (cx - btn_text.get_width() // 2, cy + 290 + 18),
        )

        # Decorative stars on either side of the button
        self._draw_star_icon((cx - btn_w // 2 - 40, cy + 290 + btn_h // 2),
                             28, (255, 209, 102))
        self._draw_star_icon((cx + btn_w // 2 + 40, cy + 290 + btn_h // 2),
                             28, (255, 209, 102))

        pygame.display.flip()

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                    return
            self.clock.tick(10)

    def _show_break(self, block_done, total_blocks):
        """Friendly break screen between blocks.

        Shows progress as a row of colored dots (filled for completed,
        empty for remaining) plus a "take a breath" prompt and a big
        continue button. No emoji characters — all decorative elements
        are drawn with pygame primitives.
        """
        self.screen.fill((255, 248, 240))
        cx = self.sw // 2
        cy = self.sh // 2

        # Decorative star crown (replaces 🌟)
        self._draw_star_icon((cx, cy - 240), 56, (255, 209, 102))

        title = self.font_big.render(
            f"第 {block_done} 关完成！", True, (43, 24, 16))
        self.screen.blit(title, (cx - title.get_width() // 2, cy - 170))

        # Visual progress dots — filled for completed, outline for remaining
        dot_r = 18
        gap = 14
        total_width = total_blocks * dot_r * 2 + (total_blocks - 1) * gap
        start_x = cx - total_width // 2
        dot_y = cy - 80
        for i in range(total_blocks):
            cx_i = start_x + i * (dot_r * 2 + gap) + dot_r
            if i < block_done:
                # Filled orange dot — no ✓ text (emoji-free)
                self._draw_aa_circle(
                    (255, 140, 66), (cx_i, dot_y), dot_r)
                # Inner white dot for visual interest
                self._draw_aa_circle(
                    (255, 255, 255), (cx_i, dot_y), dot_r // 3)
            else:
                # Empty ring
                pygame.draw.circle(
                    self.screen, (255, 209, 102), (cx_i, dot_y), dot_r,
                    width=3)

        # Rest message — no emoji
        rest_lines = [
            "休息一下，眨眨眼睛",
            f"还有 {total_blocks - block_done} 关就完成啦",
        ]
        for i, line in enumerate(rest_lines):
            t = self.font_med.render(line, True, (139, 111, 92))
            self.screen.blit(
                t, (cx - t.get_width() // 2, cy + 20 + i * 48))

        # Continue button — no emoji (▶ triangle often not in CJK fonts)
        btn_text = self.font_big.render(
            "按任意键 继续", True, (255, 255, 255))
        btn_w = btn_text.get_width() + 80
        btn_h = btn_text.get_height() + 36
        btn_rect = pygame.Rect(
            cx - btn_w // 2, cy + 160, btn_w, btn_h)
        pygame.draw.rect(
            self.screen, (0, 0, 0), btn_rect.move(0, 6),
            border_radius=btn_h // 2)
        pygame.draw.rect(
            self.screen, (78, 205, 196),
            btn_rect,
            border_radius=btn_h // 2,
        )
        self.screen.blit(
            btn_text,
            (cx - btn_text.get_width() // 2, cy + 160 + 18),
        )
        # Decorative stars on each side of the button
        self._draw_star_icon((cx - btn_w // 2 - 36, cy + 160 + btn_h // 2),
                             24, (255, 209, 102))
        self._draw_star_icon((cx + btn_w // 2 + 36, cy + 160 + btn_h // 2),
                             24, (255, 209, 102))

        pygame.display.flip()

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                    return
            self.clock.tick(10)

    # ──────────────────────────────────────
    # 单个 trial 执行
    # ──────────────────────────────────────

    def run_trial(self, trial: TrialData):
        """Run a single trial end-to-end.

        Trial structure (Wainstein 2017 / Rojas-Líbano 2019):

            Fix(500) → Enc1(750) → Fix(500) → Enc2(750) → Fix(500) →
            Enc3(750) → MaintenanceFix(500) → Distractor(500) → Probe(1500)

        The 500 ms maintenance fixation between the last encoding array and
        the distractor was added after re-reading the parent paper (see
        Wainstein 2017 Methods, "this same interval preceded the distractor").
        Without it the working-memory hold period was missing entirely.
        """
        cfg = self.cfg

        # 1. Fixation
        self._present_phase(self._draw_fixation, cfg['fixation_ms'], trial)

        # 2. Encoding: 3 arrays, each followed by an inter-array fixation
        for arr in trial.dot_positions:
            self._present_phase(lambda a=arr: self._draw_dots(a),
                                cfg['encoding_ms'], trial)
            self._present_phase(self._draw_fixation,
                                cfg['encoding_gap_ms'], trial)

        # 3. Distractor — RESOLVE ONCE before the per-frame loop so we get
        #    one image / one shape pattern, not 15 of them.
        distractor_payload = self._resolve_distractor(trial)
        self._present_phase(
            lambda: self._render_distractor(distractor_payload),
            cfg['distractor_ms'], trial)

        # 4. Probe (also collects F/J response)
        response, rt = self._present_phase(
            lambda: self._draw_probe(trial.probe_pos),
            cfg['probe_ms'], trial, check_keys=True)

        trial.response = response
        trial.reaction_time = rt
        trial.correct = 1 if response == trial.correct_answer else 0

        # 5. Feedback — kept short and configurable. Default value left at
        #    500 ms for now; can be set to 0 in TASK_CONFIG to mirror the
        #    original paradigm exactly (the original paper does not include
        #    trial-level feedback).
        if cfg.get('feedback_ms', 0) > 0:
            self._present_phase(
                lambda: self._draw_feedback(trial.correct == 1),
                cfg['feedback_ms'])

    # ──────────────────────────────────────
    # 完整实验执行
    # ──────────────────────────────────────

    def _set_window_opaque(self, opaque: bool) -> None:
        """Win32-only: toggle the layered window between solid and translucent.

        On macOS / Linux this is a no-op (the window is always opaque).
        """
        if not _IS_WIN32 or self.gs.hwnd is None:
            return
        try:
            import ctypes  # local import — Win32 only
            alpha = 255 if opaque else 150
            ctypes.windll.user32.SetLayeredWindowAttributes(
                self.gs.hwnd, 0x000000, alpha, 0x2)
        except Exception as exc:  # pragma: no cover
            print(f"[sternberg] Win32 layering toggle failed: {exc}")

    def run(self):
        """Run the full Sternberg experiment (160 trials)."""
        print("=" * 55)
        print("  Sternberg 视觉空间工作记忆任务")
        print("=" * 55)

        self.generate_trials()
        n_total = len(self.trials)
        print(f"  {n_total} trials, {self.cfg['n_blocks']} blocks")
        self._emit("task_start", total_trials=n_total,
                   n_blocks=self.cfg['n_blocks'])

        # Make the window fully opaque during the task (Win32 only)
        self._set_window_opaque(True)

        # Start the background capture worker so the main draw loop
        # never blocks on camera / MediaPipe / AFFNet. This is the fix
        # for the "stuttering at phase transitions" issue.
        self._capture_worker = _CaptureWorker(self.gs)
        self._capture_worker.start()

        try:
            self._show_instructions()

            current_block = 0
            for i, trial in enumerate(self.trials):
                # Block break
                if trial.block_num > current_block:
                    if current_block > 0:
                        self._emit("block_end", block=current_block)
                        self._show_break(current_block, self.cfg['n_blocks'])
                    current_block = trial.block_num
                    print(f"  第 {current_block} 区组...", end="", flush=True)
                    self._emit("block_start", block=current_block)

                self._emit("trial_start", trial_num=trial.trial_num,
                           block=trial.block_num, load=trial.load)
                self.run_trial(trial)
                self.results.append(trial)
                self._emit("trial_end", trial_num=trial.trial_num,
                           correct=int(trial.correct),
                           rt=float(trial.reaction_time)
                               if not np.isnan(trial.reaction_time) else None)

                # Per-block progress print
                if (i + 1) % self.cfg['trials_per_block'] == 0:
                    block_results = self.results[-self.cfg['trials_per_block']:]
                    n_correct = sum(1 for t in block_results if t.correct)
                    n_resp = sum(1 for t in block_results
                                 if t.response is not None)
                    print(f" accuracy={n_correct}/{n_resp}")

            # End screen — celebratory warm background with drawn trophy
            # + star row. No emoji characters — see the decorative-icon
            # helpers for why (Chinese fonts don't have emoji glyphs).
            self.screen.fill((255, 248, 240))
            cx, cy = self.sw // 2, self.sh // 2

            # Big drawn trophy in the middle top
            self._draw_trophy_icon((cx, cy - 150), 180)

            title = self.font_big.render(
                "全部完成啦！", True, (43, 24, 16))
            self.screen.blit(
                title, (cx - title.get_width() // 2, cy - 20))

            sub = self.font_med.render(
                "你真棒，正在生成报告…", True, (139, 111, 92))
            self.screen.blit(
                sub, (cx - sub.get_width() // 2, cy + 60))

            # Three decorative stars
            star_spacing = 80
            for i, offset in enumerate((-star_spacing, 0, star_spacing)):
                size = 48 if i == 1 else 36  # middle star bigger
                self._draw_star_icon(
                    (cx + offset, cy + 160), size, (255, 209, 102))

            pygame.display.flip()
            pygame.time.wait(2500)
            self._emit("task_done", total_trials=n_total)

        finally:
            # Stop the background capture worker cleanly
            if self._capture_worker is not None:
                self._capture_worker.stop()
                self._capture_worker.join(timeout=2.0)
                self._capture_worker = None
            self._set_window_opaque(False)

        # 行为汇总
        n_resp = sum(1 for t in self.results if t.response is not None)
        n_correct = sum(1 for t in self.results if t.correct)
        rts = [t.reaction_time for t in self.results
               if not np.isnan(t.reaction_time)]
        print(f"\n  汇总: {n_correct}/{n_resp} 正确 "
              f"({n_correct / max(n_resp, 1) * 100:.1f}%), "
              f"平均RT={np.mean(rts):.3f}s" if rts else "")

        return self.results

    # ──────────────────────────────────────
    # 数据保存
    # ──────────────────────────────────────

    def save_behavioral_csv(self, filepath):
        """保存行为数据到 CSV。"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'trial', 'block', 'load', 'distractor_type',
                'distractor_name', 'image_file',
                'correct_answer', 'response', 'correct', 'reaction_time',
                'probe_pos', 'is_target',
            ])
            for t in self.results:
                writer.writerow([
                    t.trial_num, t.block_num, t.load, t.distractor_type,
                    t.distractor_name, t.image_file,
                    t.correct_answer, t.response or '',
                    t.correct,
                    f'{t.reaction_time:.4f}' if not np.isnan(t.reaction_time)
                    else '',
                    str(t.probe_pos), int(t.is_target),
                ])
        print(f"  行为数据CSV: {filepath}")
