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
import random
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
                        image_pools: dict,
                        pool_indices: dict,
                        target_size: Tuple[int, int],
                        ) -> Tuple[Optional[pygame.Surface], str]:
    """
    根据干扰类型获取刺激 Surface 和图片文件名。

    Args:
        dtype: 干扰类型字符串
        image_pools: {'neutral': [path,...], 'emotional': [path,...]}
        pool_indices: {'neutral': int, 'emotional': int}  循环索引
        target_size: 图片统一尺寸 (w, h)

    Returns:
        (surface_or_None, image_filename)
    """
    if dtype == "neutral_image" and image_pools['neutral']:
        pool = image_pools['neutral']
        idx = pool_indices['neutral'] % len(pool)
        pool_indices['neutral'] = idx + 1
        path = pool[idx]
        surf = _load_and_prepare_image(path, target_size)
        return surf, os.path.basename(path)

    if dtype == "emotional_image" and image_pools['emotional']:
        pool = image_pools['emotional']
        idx = pool_indices['emotional'] % len(pool)
        pool_indices['emotional'] = idx + 1
        path = pool[idx]
        surf = _load_and_prepare_image(path, target_size)
        return surf, os.path.basename(path)

    return None, ""


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
        self.image_pools = load_images(stimuli_dir)
        for k in self.image_pools:
            random.shuffle(self.image_pools[k])
        self.pool_indices = {'neutral': 0, 'emotional': 0}

        self.trials: List[TrialData] = []
        self.results: List[TrialData] = []

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
        self.screen.fill((0, 0, 0))
        cx, cy = self.sw // 2, self.sh // 2
        sz = int(self.sh * 0.025)
        pygame.draw.line(self.screen, (255, 255, 255),
                         (cx - sz, cy), (cx + sz, cy), 3)
        pygame.draw.line(self.screen, (255, 255, 255),
                         (cx, cy - sz), (cx, cy + sz), 3)

    def _draw_dots(self, positions):
        self.screen.fill((0, 0, 0))
        for r, c in positions:
            px, py = self._grid_to_px(r, c)
            pygame.draw.circle(self.screen, (255, 255, 255), (px, py), self.dot_r)

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
                dtype, self.image_pools, self.pool_indices,
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
        px, py = self._grid_to_px(*pos)
        pygame.draw.circle(self.screen, (255, 220, 50), (px, py), self.dot_r + 3)
        hint = self.font_sm.render("F = 是    J = 否", True, (150, 150, 150))
        self.screen.blit(hint, (self.sw // 2 - hint.get_width() // 2, self.sh - 60))

    def _draw_feedback(self, is_correct):
        self.screen.fill((0, 0, 0))
        if is_correct:
            t = self.font_big.render("正确", True, (50, 255, 50))
        else:
            t = self.font_big.render("错误", True, (255, 50, 50))
        self.screen.blit(t, (self.sw // 2 - t.get_width() // 2,
                             self.sh // 2 - t.get_height() // 2))

    # ──────────────────────────────────────
    # 帧级数据采集
    # ──────────────────────────────────────

    def _capture_one_frame(self):
        """
        采集一帧摄像头，返回 (gaze_x, gaze_y, pupil_proxy)。
        利用 GazeSystem 已校准的回归器预测注视点，
        利用 FrameFeature.pupil_proxy 获取瞳孔估计。
        """
        ok, frame = self.gs.cap.read()
        if not ok:
            return np.nan, np.nan, np.nan

        ff = self.gs._extract(frame)
        if ff is None:
            return np.nan, np.nan, np.nan

        self.gs.feat_buf.append(ff.feat)
        pupil = ff.pupil_proxy

        # 注视预测
        gx, gy = np.nan, np.nan
        if len(self.gs.feat_buf) >= 3:
            feat = self.gs._weighted_mean_feat()
            mx, my = self.gs._predict_screen_point(feat)
            sx, sy = self.gs.smoother.update(float(mx), float(my))
            gx = float(np.clip(sx, 0, self.sw - 1))
            gy = float(np.clip(sy, 0, self.sh - 1))

        return gx, gy, pupil

    def _present_phase(self, draw_fn, duration_ms, trial=None, check_keys=False):
        """
        呈现一个刺激阶段，同时持续采集眼动数据。

        Args:
            draw_fn: 绘制刺激的函数
            duration_ms: 持续时间 (ms)
            trial: TrialData 实例，若非 None 则记录注视/瞳孔数据
            check_keys: 若 True 则检测 F/J 按键响应

        Returns:
            (response_key, reaction_time) if check_keys else (None, None)
        """
        fps = self.cfg['target_fps']
        n_frames = max(1, int(duration_ms * fps / 1000))
        response = None
        rt = float('nan')
        t_start = time.perf_counter()

        for _ in range(n_frames):
            # 事件处理
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

            # 眼动数据采集
            gx, gy, pupil = self._capture_one_frame()
            if trial is not None:
                trial.gaze_x_series.append(gx)
                trial.gaze_y_series.append(gy)
                trial.pupil_series.append(pupil)

            # 绘制
            draw_fn()
            pygame.display.flip()
            self.clock.tick(fps)

        return response, rt

    # ──────────────────────────────────────
    # 指导语与休息屏幕
    # ──────────────────────────────────────

    def _show_instructions(self):
        self.screen.fill((0, 0, 0))
        lines = [
            "视觉空间工作记忆任务",
            "",
            "每个试次流程：",
            "  1. 注视屏幕中央的十字 (+)",
            "  2. 记住圆点的位置（共 3 屏）",
            "  3. 忽略干扰画面",
            "  4. 出现探测圆点时：",
            "     按 F 键 —— 该位置出现过",
            "     按 J 键 —— 该位置没出现过",
            "",
            f"共 {self.cfg['n_blocks']} 个区组 × "
            f"{self.cfg['trials_per_block']} 个试次",
            "",
            "按 空格键 开始",
        ]
        y = self.sh // 2 - len(lines) * 18
        for line in lines:
            if line:
                t = self.font_sm.render(line, True, (220, 220, 220))
                self.screen.blit(t, (self.sw // 2 - t.get_width() // 2, y))
            y += 36
        pygame.display.flip()

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                    if event.key == pygame.K_SPACE:
                        return
            self.clock.tick(10)

    def _show_break(self, block_done, total_blocks):
        self.screen.fill((0, 0, 0))
        lines = [
            f"第 {block_done} / {total_blocks} 区组已完成",
            "",
            "请稍作休息",
            "",
            "按 空格键 继续",
        ]
        y = self.sh // 2 - len(lines) * 25
        for line in lines:
            if line:
                t = self.font_med.render(line, True, (200, 200, 200))
                self.screen.blit(t, (self.sw // 2 - t.get_width() // 2, y))
            y += 50
        pygame.display.flip()

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                    if event.key == pygame.K_SPACE:
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

            # End screen
            self.screen.fill((0, 0, 0))
            t = self.font_big.render("任务完成！", True, (100, 255, 100))
            self.screen.blit(t, (self.sw // 2 - t.get_width() // 2,
                                 self.sh // 2 - 30))
            pygame.display.flip()
            pygame.time.wait(2000)
            self._emit("task_done", total_trials=n_total)

        finally:
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
