"""Gaze tracking system — refactored from ``model/eye_tracker/itracker.py``.

Migration notes (see plan §1.5, §1.6):

* Removed Windows-only ``ctypes.windll.user32`` calls (wrapped in ``if win32:``).
* Replaced ``C:\\Windows\\Fonts\\msyh.ttc`` font path with cross-platform lookup
  via :func:`adhd_engine.worker.fonts.get_chinese_font_path`.
* Replaced ``D:\\Study\\itracker\\...`` ECML output paths with
  ``platformdirs`` per-user directories under
  :data:`adhd_engine.config.USER_DATA_DIR`.
* Added 5-frame pupil smoothing buffer to reduce webcam noise (plan §1.5.5).
* Added ``ipc_publisher`` callable hook so each captured gaze frame can be
  pushed to a parent process via ``multiprocessing.Queue``.
* Added graceful fallback to "geometry-only" mode when the AFFNet class
  definition is unavailable (the trained ``affnet.pth.tar`` requires the
  GazeTrack training repo which is not vendored here). In fallback mode the
  ``model_gaze`` 2D component is zeros; the Ridge calibration regressor still
  works because it learns to ignore those dimensions.
"""

import os
import sys
import time
import glob
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pygame
import torch
from PIL import Image
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from torchvision import transforms

try:
    from google.protobuf import message_factory as _pb_message_factory

    if not hasattr(_pb_message_factory.MessageFactory, "GetPrototype"):
        def _get_prototype(self, descriptor):
            return _pb_message_factory.GetMessageClass(descriptor)

        _pb_message_factory.MessageFactory.GetPrototype = _get_prototype  # type: ignore[attr-defined]
except Exception:
    pass

import mediapipe as mp

from adhd_engine import config as engine_config
from adhd_engine.worker.fonts import get_chinese_font_path

_IS_WIN32 = sys.platform.startswith("win")

# Sentinel returned by ModelAdapter.predict when the deep model is unavailable
# (we run in geometry-only mode). Two zeros so the 8D feature vector layout
# is preserved.
_ZERO_GAZE = np.zeros(2, dtype=np.float32)


def _load_simplecnn():
    """Lazy import the SimpleCNN training-side helper.

    Only invoked when MODEL_TYPE='simplecnn'. The implementation lives in the
    GazeTrack training repo, which is not vendored — we degrade gracefully.
    """
    try:
        from model.cnn import simplecnn  # type: ignore
        return simplecnn
    except ImportError as exc:
        raise RuntimeError(
            "SimpleCNN class not found (model.cnn module missing). "
            "Use MODEL_TYPE='auto' for AFFNet+fallback, or install the "
            "GazeTrack training repo."
        ) from exc


# =========================
# Runtime config
# =========================
CHECKPOINT_PATH = str(engine_config.AFFNET_CHECKPOINT)
MODEL_TYPE = "auto"  # auto | simplecnn | AFFNet | none
CAMERA_ID = 0
TARGET_FPS = 30
CALI_SAMPLES_PER_POINT = 30
CALI_MIN_VALID_PER_POINT = 15
TRACKING_DURATION_SEC = 1200  # 20 minutes
POINT_RADIUS = 22
HEATMAP_BLUR_KSIZE = 121
FEAT_BUF_SIZE = 10            # feature buffer length
FIXATION_VEL_THRESH = 25.0    # px/frame velocity threshold for fixation
FIXATION_ALPHA = 0.05
SACCADE_ALPHA = 0.35

# Plan §1.5.5 — pupil signal smoothing window
PUPIL_SMOOTH_WINDOW = engine_config.PUPIL_SMOOTH_WINDOW

# =========================
# ECML-HBN compatible output (now under per-user data dir)
# =========================
ECML_DATA_ROOT = engine_config.USER_DATA_DIR / "ecml_data"
NEW_PATIENT_DATA_DIR = ECML_DATA_ROOT / "new_patients"
NEW_PATIENT_VIDEO_DIR = NEW_PATIENT_DATA_DIR / "videos"
ECML_OUTPUT_DIR = NEW_PATIENT_DATA_DIR / "X_px"
ECML_TIMESTAMPS_DIR = NEW_PATIENT_DATA_DIR / "timestamps"
ECML_METADATA_DIR = NEW_PATIENT_DATA_DIR / "metadata"
ECML_HEATMAP_DIR = NEW_PATIENT_DATA_DIR / "heatmaps"
ECML_REPORT_DIR = NEW_PATIENT_DATA_DIR / "reports"
SUBJECT_ID = "SUBJECT_001"
ADHD_MODEL_DIR = str(engine_config.SAVED_MODELS_DIR)

# HBN 视频协议：可配置视频路径 (用于自由观看范式)
# 设为 None 时使用纯注视追踪模式
VIDEO_STIMULUS_PATH = None   # 例: os.path.join(NEW_PATIENT_VIDEO_DIR, "Despicable_Me.mp4")
VIDEO_STIMULUS_NAME = "Free_Viewing"  # 视频名称标签
OFFICIAL_VIDEO_NAMES = [
    "Diary_of_a_Wimpy_Kid_Trailer",
    "Fractals",
    "Despicable_Me",
    "The_Present",
]


# 13点校准，通常比9点更稳健
CALI_GRID = [
    (0.08, 0.08), (0.5, 0.08), (0.92, 0.08),
    (0.08, 0.32), (0.5, 0.32), (0.92, 0.32),
    (0.08, 0.5), (0.5, 0.5), (0.92, 0.5),
    (0.08, 0.68), (0.5, 0.68), (0.92, 0.68),
    (0.5, 0.92),
]


LEFT_EYE_INDICES = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_INDICES = [33, 160, 158, 133, 153, 144]
LEFT_EAR = [33, 160, 158, 133, 153, 144]
RIGHT_EAR = [362, 385, 387, 263, 373, 380]

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


@dataclass
class FrameFeature:
    feat: np.ndarray
    quality: float
    pupil_proxy: float = 0.0


class FixationSmoother:
    """注视感知自适应平滑器：注视时极稳，扫视时跟随。"""

    def __init__(self):
        self.x = None
        self.y = None

    def update(self, raw_x: float, raw_y: float) -> Tuple[float, float]:
        if self.x is None:
            self.x, self.y = raw_x, raw_y
            return raw_x, raw_y

        dx, dy = raw_x - self.x, raw_y - self.y
        vel = np.hypot(dx, dy)

        if vel < FIXATION_VEL_THRESH:
            # 注视态：极低跟随，保持稳定
            alpha = FIXATION_ALPHA
        else:
            # 扫视态：更快跟随，但仍然受控
            alpha = min(SACCADE_ALPHA, vel / 300.0)

        self.x += alpha * dx
        self.y += alpha * dy
        return self.x, self.y


def add_gazetrack_to_path() -> None:
    """Add the GazeTrack training repo to ``sys.path`` if present.

    Searches a few candidate locations relative to the engine package and the
    parent workspace. The training repo is not vendored in this codebase — if
    nothing is found we silently return and ModelAdapter will fall back to
    geometry-only mode (see plan migration notes at the top of this file).
    """
    candidates = [
        engine_config.REPO_ROOT.parent / "itraker" / "GazeTrack",
        engine_config.REPO_ROOT / "itraker" / "GazeTrack",
        engine_config.REPO_ROOT / "train_model" / "GazeTrack",
        engine_config.REPO_ROOT / "model" / "train_model" / "GazeTrack",
    ]
    for path in candidates:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
            return


def strip_module_prefix(state_dict: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    has_module_prefix = all(k.startswith("module.") for k in state_dict.keys())
    if not has_module_prefix:
        return state_dict
    return {k.replace("module.", "", 1): v for k, v in state_dict.items()}


def checkpoint_to_state_dict(ckpt_obj: object) -> Dict[str, torch.Tensor]:
    if isinstance(ckpt_obj, dict) and "state_dict" in ckpt_obj:
        state = ckpt_obj["state_dict"]
    elif isinstance(ckpt_obj, dict):
        state = ckpt_obj
    else:
        raise RuntimeError("Unsupported checkpoint format")
    return strip_module_prefix(state)


def infer_model_type(state_dict: Dict[str, torch.Tensor]) -> str:
    keys = list(state_dict.keys())
    if any(k.startswith("eyeModel.") or k.startswith("faceModel.") for k in keys):
        return "AFFNet"
    if any(k.startswith("features.") or k.startswith("fc.") for k in keys):
        return "simplecnn"
    raise RuntimeError("Cannot infer model type from checkpoint, set MODEL_TYPE manually")


def build_model(model_type: str):
    if model_type == "simplecnn":
        simplecnn = _load_simplecnn()
        return simplecnn()
    if model_type == "AFFNet":
        add_gazetrack_to_path()
        from model.AFFNet import AFFNet  # type: ignore  # noqa: I001
        return AFFNet()
    raise RuntimeError(f"Unsupported model type: {model_type}")


class ModelAdapter:
    """AFFNet/SimpleCNN inference wrapper with graceful fallback.

    If the deep model can't be built (the GazeTrack training repo is not on
    ``sys.path``), the adapter switches to ``model_type='none'`` and returns a
    zero gaze vector for every call. The Ridge calibration regressor in
    ``GazeSystem`` then learns from the 6D geometric features alone.
    """

    def __init__(self, checkpoint_path: str, model_type: str):
        try:
            ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
            state_dict = checkpoint_to_state_dict(ckpt)
            inferred = infer_model_type(state_dict) if model_type == "auto" else model_type
            self.model = build_model(inferred).to(device)
            missing, unexpected = self.model.load_state_dict(state_dict, strict=False)
            self.model.eval()
            self.model_type = inferred
            if missing or unexpected:
                print(f"[WARN] state_dict mismatch, missing={len(missing)}, unexpected={len(unexpected)}")
            print(f"[gaze_system] Loaded deep model: {self.model_type}")
        except Exception as exc:
            # Plan migration note — fall back to geometry-only mode
            print(
                f"[gaze_system] WARNING: deep model unavailable "
                f"({exc.__class__.__name__}: {exc}). "
                "Falling back to geometry-only gaze (6D iris+head pose only). "
                "Add the GazeTrack training repo at <repo>/itraker/GazeTrack/ "
                "to enable AFFNet."
            )
            self.model = None
            self.model_type = "none"

        self.simple_tf = transforms.Compose([
            transforms.Resize([224, 224]),
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ])
        self.aff_eye_tf = transforms.Compose([
            transforms.Resize([112, 112]),
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ])
        self.aff_face_tf = transforms.Compose([
            transforms.Resize([224, 224]),
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ])

    def predict(self, left_eye, right_eye, face_img=None, rect_vec=None):
        if self.model_type == "none" or self.model is None:
            return _ZERO_GAZE.copy()

        with torch.no_grad():
            if self.model_type == "simplecnn":
                left = self.simple_tf(Image.fromarray(cv2.cvtColor(left_eye, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(device)
                right = self.simple_tf(Image.fromarray(cv2.cvtColor(right_eye, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(device)
                gaze = (self.model(left) + self.model(right)) / 2.0
                return gaze.cpu().numpy().flatten()

            left = self.aff_eye_tf(Image.fromarray(cv2.cvtColor(left_eye, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(device)
            right = self.aff_eye_tf(Image.fromarray(cv2.cvtColor(right_eye, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(device)
            face = self.aff_face_tf(Image.fromarray(cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(device)
            rect = torch.from_numpy(rect_vec.astype(np.float32)).unsqueeze(0).to(device)
            gaze = self.model(left, right, face, rect)
            return gaze.cpu().numpy().flatten()


def crop_rect(img, x_min, y_min, x_max, y_max):
    h, w = img.shape[:2]
    x_min = max(0, min(w - 1, x_min))
    x_max = max(1, min(w, x_max))
    y_min = max(0, min(h - 1, y_min))
    y_max = max(1, min(h, y_max))
    if x_max <= x_min or y_max <= y_min:
        return None
    return img[y_min:y_max, x_min:x_max]


def eye_rect_from_landmarks(landmarks, indices, w, h, margin=6):
    pts = np.array([(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in indices], dtype=np.int32)
    x_min = int(np.min(pts[:, 0])) - margin
    x_max = int(np.max(pts[:, 0])) + margin
    y_min = int(np.min(pts[:, 1])) - margin
    y_max = int(np.max(pts[:, 1])) + margin
    return x_min, y_min, x_max, y_max


def face_rect_from_landmarks(landmarks, w, h, margin=12):
    pts = np.array([(int(p.x * w), int(p.y * h)) for p in landmarks], dtype=np.int32)
    x_min = int(np.min(pts[:, 0])) - margin
    x_max = int(np.max(pts[:, 0])) + margin
    y_min = int(np.min(pts[:, 1])) - margin
    y_max = int(np.max(pts[:, 1])) + margin
    return x_min, y_min, x_max, y_max


def rect_feature(lrect_xyxy, rrect_xyxy, frect_xyxy, w, h):
    lx1, ly1, lx2, ly2 = lrect_xyxy
    rx1, ry1, rx2, ry2 = rrect_xyxy
    fx1, fy1, fx2, fy2 = frect_xyxy
    lw, lh = max(1, lx2 - lx1), max(1, ly2 - ly1)
    rw, rh = max(1, rx2 - rx1), max(1, ry2 - ry1)
    fw, fh = max(1, fx2 - fx1), max(1, fy2 - fy1)
    return np.array([
        lx1 / w, ly1 / h, lw / w, lh / h,
        rx1 / w, ry1 / h, rw / w, rh / h,
        fx1 / w, fy1 / h, fw / w, fh / h,
    ], dtype=np.float32)


def compute_ear(landmarks, indices):
    p1, p2, p3, p4, p5, p6 = [landmarks[i] for i in indices]
    p1 = np.array([p1.x, p1.y])
    p2 = np.array([p2.x, p2.y])
    p3 = np.array([p3.x, p3.y])
    p4 = np.array([p4.x, p4.y])
    p5 = np.array([p5.x, p5.y])
    p6 = np.array([p6.x, p6.y])
    vertical = np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)
    horizontal = max(1e-6, np.linalg.norm(p1 - p4))
    return float(vertical / (2.0 * horizontal))


def compute_iris_features(landmarks):
    """虹膜位置比率：沿眼轴和垂直方向的归一化坐标，头部旋转不变。"""
    def eye_ratio(c1_idx, c2_idx, iris_idx):
        c1 = np.array([landmarks[c1_idx].x, landmarks[c1_idx].y])
        c2 = np.array([landmarks[c2_idx].x, landmarks[c2_idx].y])
        iris = np.array([landmarks[iris_idx].x, landmarks[iris_idx].y])
        axis = c2 - c1
        length = np.linalg.norm(axis)
        if length < 1e-6:
            return None
        axis_u = axis / length
        normal = np.array([-axis_u[1], axis_u[0]])
        offset = iris - c1
        rx = np.dot(offset, axis_u) / length
        ry = np.dot(offset, normal) / length
        return np.array([rx, ry], dtype=np.float32)

    # 右眼（解剖）: 外角33, 内角133, 虹膜中心468
    r_ratio = eye_ratio(33, 133, 468)
    # 左眼（解剖）: 内角362, 外角263, 虹膜中心473
    l_ratio = eye_ratio(362, 263, 473)
    if r_ratio is None or l_ratio is None:
        return None

    # 头部朝向：鼻尖相对于面部中心的归一化偏移
    nose = np.array([landmarks[1].x, landmarks[1].y])
    chin = np.array([landmarks[152].x, landmarks[152].y])
    forehead = np.array([landmarks[10].x, landmarks[10].y])
    left_cheek = np.array([landmarks[234].x, landmarks[234].y])
    right_cheek = np.array([landmarks[454].x, landmarks[454].y])
    face_w = max(1e-6, abs(right_cheek[0] - left_cheek[0]))
    face_h = max(1e-6, abs(chin[1] - forehead[1]))
    face_cx = (left_cheek[0] + right_cheek[0]) / 2.0
    face_cy = (forehead[1] + chin[1]) / 2.0
    head_yaw = (nose[0] - face_cx) / face_w
    head_pitch = (nose[1] - face_cy) / face_h

    return np.concatenate([r_ratio, l_ratio, [head_yaw, head_pitch]]).astype(np.float32)


def estimate_pupil_size(landmarks):
    """从 MediaPipe 虹膜地标估算相对瞳孔/虹膜大小 (瞳孔扩张代理指标)。"""
    try:
        # 右虹膜直径 (水平 469-471, 垂直 470-472)
        r_h = np.linalg.norm(np.array([landmarks[469].x, landmarks[469].y]) -
                             np.array([landmarks[471].x, landmarks[471].y]))
        r_v = np.linalg.norm(np.array([landmarks[470].x, landmarks[470].y]) -
                             np.array([landmarks[472].x, landmarks[472].y]))
        # 左虹膜直径 (474-476, 475-477)
        l_h = np.linalg.norm(np.array([landmarks[474].x, landmarks[474].y]) -
                             np.array([landmarks[476].x, landmarks[476].y]))
        l_v = np.linalg.norm(np.array([landmarks[475].x, landmarks[475].y]) -
                             np.array([landmarks[477].x, landmarks[477].y]))
        # 用眼宽归一化 (消除距离影响)
        r_eye = max(1e-6, np.linalg.norm(
            np.array([landmarks[33].x, landmarks[33].y]) -
            np.array([landmarks[133].x, landmarks[133].y])))
        l_eye = max(1e-6, np.linalg.norm(
            np.array([landmarks[362].x, landmarks[362].y]) -
            np.array([landmarks[263].x, landmarks[263].y])))
        r_ratio = (r_h + r_v) / (2 * r_eye)
        l_ratio = (l_h + l_v) / (2 * l_eye)
        return float((r_ratio + l_ratio) / 2.0)
    except (IndexError, Exception):
        return 0.0


def build_regressor():
    return Pipeline([
        ("scale", StandardScaler()),
        ("ridge", Ridge(alpha=1.0)),
    ])


class GazeSystem:
    """Gaze tracking + UI orchestration.

    The constructor opens the camera, loads MediaPipe and the deep model,
    and brings up a fullscreen pygame window. On macOS pygame **must** run on
    the main thread, so a parent FastAPI process should spawn this class in a
    subprocess (see plan §1.2). The ``ipc_publisher`` callable, if provided,
    is invoked once per captured frame with the smoothed
    ``(t, x, y, pupil, valid)`` tuple — used by the worker runner to forward
    real-time gaze frames to subscribed WebSocket clients.
    """

    def __init__(
        self,
        subject_id: str = SUBJECT_ID,
        video_path: Optional[str] = None,
        ipc_publisher: Optional[Callable[[float, float, float, float, bool], None]] = None,
        session_id: Optional[str] = None,
    ):
        self.subject_id = subject_id
        self.session_id = session_id
        self.ipc_publisher = ipc_publisher
        self.video_path = self._resolve_video_path(video_path)
        self.model = ModelAdapter(CHECKPOINT_PATH, MODEL_TYPE)
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.cap = cv2.VideoCapture(CAMERA_ID)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Verify the camera actually opened — on macOS this is the place
        # where missing TCC permission shows up. We probe a single read so
        # the worker can fail fast with a clear message instead of silently
        # losing every calibration sample to a black-feed loop.
        if not self.cap.isOpened():
            raise RuntimeError(
                "camera_unavailable: cv2.VideoCapture(0) could not open the "
                "default camera. On macOS this usually means the launching "
                "Terminal app does not have camera permission. Open System "
                "Settings → Privacy & Security → Camera and enable Terminal "
                "(or your terminal app), then quit and reopen Terminal."
            )
        ok, _probe = self.cap.read()
        if not ok:
            self.cap.release()
            raise RuntimeError(
                "camera_unavailable: cv2.VideoCapture.read() returned False. "
                "Check System Settings → Privacy & Security → Camera and "
                "grant access to the terminal app launching this engine, "
                "then quit and reopen the terminal."
            )

        pygame.init()
        pygame.font.init()
        info = pygame.display.Info()
        self.sw, self.sh = info.current_w, info.current_h
        # Use a regular fullscreen mode (not NOFRAME) on macOS so the window
        # actually grabs focus and gets composited. NOFRAME borderless windows
        # behave erratically with macOS Mission Control / Metal compositor.
        if _IS_WIN32:
            display_flags = pygame.NOFRAME
        else:
            display_flags = pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((self.sw, self.sh), display_flags)
        pygame.display.set_caption("研究用眼动追踪器 — ADHD 筛查")
        # Initial paint so the user sees something immediately and confirms the
        # window is on the right display.
        self.screen.fill((25, 25, 25))
        pygame.display.flip()

        # Cross-platform Chinese font (plan §1.6)
        zh_font_path = get_chinese_font_path()
        if zh_font_path is not None:
            self.ui_font = pygame.font.Font(zh_font_path, 32)
        else:
            self.ui_font = pygame.font.SysFont(
                "pingfangsc,microsoftyahei,simhei,simsun,notosanscjksc", 32
            )

        # Windows-only window-layering tricks — keep them only on win32
        self.hwnd = None
        if _IS_WIN32:
            try:
                import ctypes  # local import to avoid loading on POSIX
                self.hwnd = pygame.display.get_wm_info()["window"]
                ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, self.sw, self.sh, 0x0001)
                ctypes.windll.user32.SetWindowLongW(self.hwnd, -20, 0x00080000)
                ctypes.windll.user32.SetLayeredWindowAttributes(self.hwnd, 0x000000, 150, 0x2)
            except Exception as exc:
                print(f"[gaze_system] Win32 layering setup failed: {exc}")

        self.calibration_points = [(int(x * self.sw), int(y * self.sh)) for x, y in CALI_GRID]
        self.regressor = build_regressor()
        self.calibrated = False

        self.smoother = FixationSmoother()
        self.feat_buf = deque(maxlen=FEAT_BUF_SIZE)
        self.tracked_points: List[Tuple[int, int]] = []

        # Plan §1.5.5 — pupil 5-frame smoothing buffer
        self._pupil_smooth_buf: deque = deque(maxlen=PUPIL_SMOOTH_WINDOW)
        self._t0 = time.monotonic()
        self.gaze_zone = "center"  # up / down / left / right / center

        # ===== ECML-HBN 兼容数据记录 =====
        self.raw_gaze_log: List[np.ndarray] = []      # 每帧 [x, y]，NaN 表示丢失
        self.timestamp_log: List[float] = []           # 每帧时间戳 (秒)
        self.video_name = VIDEO_STIMULUS_NAME

        # 视频刺激播放
        self.video_cap = None
        if self.video_path and os.path.isfile(self.video_path):
            self.video_cap = cv2.VideoCapture(self.video_path)
            self.video_name = os.path.splitext(os.path.basename(self.video_path))[0]
            print(f"视频刺激: {self.video_name}")

    def _resolve_video_path(self, video_path: Optional[str]) -> Optional[str]:
        if not video_path:
            return None
        if os.path.isfile(video_path):
            return video_path

        candidate_names = [video_path]
        base_name, ext = os.path.splitext(video_path)
        if not ext:
            candidate_names.extend([f"{video_path}.mp4", f"{video_path}.avi", f"{video_path}.mov"])

        for candidate in candidate_names:
            candidate_path = os.path.join(NEW_PATIENT_VIDEO_DIR, candidate)
            if os.path.isfile(candidate_path):
                return candidate_path
        return video_path

    def _extract(self, frame) -> Optional[FrameFeature]:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        out = self.face_mesh.process(rgb)
        if not out.multi_face_landmarks:
            return None

        lm = out.multi_face_landmarks[0].landmark

        # EAR 眨眼过滤
        l_ear = compute_ear(lm, LEFT_EAR)
        r_ear = compute_ear(lm, RIGHT_EAR)
        ear = 0.5 * (l_ear + r_ear)
        if ear < 0.12:
            return None

        # === 主特征：虹膜位置比率 + 头部朝向 (6D) ===
        iris_feat = compute_iris_features(lm)
        if iris_feat is None:
            return None

        # === 辅助特征：模型 gaze 输出 (2D) ===
        model_gaze = np.zeros(2, dtype=np.float32)
        try:
            l_xyxy = eye_rect_from_landmarks(lm, LEFT_EYE_INDICES, w, h)
            r_xyxy = eye_rect_from_landmarks(lm, RIGHT_EYE_INDICES, w, h)
            f_xyxy = face_rect_from_landmarks(lm, w, h)
            left_eye = crop_rect(frame, *l_xyxy)
            right_eye = crop_rect(frame, *r_xyxy)
            face_img = crop_rect(frame, *f_xyxy)
            if left_eye is not None and right_eye is not None:
                if self.model.model_type == "simplecnn":
                    model_gaze = self.model.predict(left_eye, right_eye)
                elif face_img is not None:
                    rect = rect_feature(l_xyxy, r_xyxy, f_xyxy, w, h)
                    model_gaze = self.model.predict(left_eye, right_eye, face_img=face_img, rect_vec=rect)
        except Exception:
            pass

        # === Pupil proxy estimate (5-frame smoothed, plan §1.5.5) ===
        pupil_raw = estimate_pupil_size(lm)
        pupil = self._smooth_pupil(pupil_raw)

        feat = np.concatenate([iris_feat, model_gaze.astype(np.float32)])  # 8D
        quality = float(np.clip((ear - 0.12) / 0.2, 0.0, 1.0))
        return FrameFeature(feat=feat, quality=quality, pupil_proxy=pupil)

    def _smooth_pupil(self, raw: float) -> float:
        """5-frame moving average for the pupil proxy.

        Plan §1.5.5 — webcam iris-landmark estimates are far noisier than the
        Tobii pupil diameter the classifier was trained on. A short moving
        average tames per-frame jitter without distorting slow trends, which
        keeps argmax-based features (`pupil_peak_latency`) honest.
        """
        if raw is None or (isinstance(raw, float) and (np.isnan(raw) or raw == 0.0)):
            # Don't pollute the buffer with detection failures
            return raw if raw is not None else float("nan")
        self._pupil_smooth_buf.append(float(raw))
        return float(np.mean(self._pupil_smooth_buf))

    def _publish_gaze(self, x: float, y: float, pupil: float, valid: bool) -> None:
        """Forward a gaze sample through the IPC publisher hook, if any."""
        if self.ipc_publisher is None:
            return
        try:
            t = time.monotonic() - self._t0
            self.ipc_publisher(t, x, y, pupil, valid)
        except Exception as exc:  # pragma: no cover - never block the loop
            print(f"[gaze_system] ipc_publisher raised: {exc}")

    def _draw_cali_point(self, point, idx, total, status="请点击圆点"):
        # Plain 3-tuple fill — the previous (0,0,0,0) tuple was a no-op on
        # macOS Metal but caused screen flicker on some setups.
        self.screen.fill((20, 20, 30))

        # Centered top banner so the user always sees status text regardless
        # of where the calibration dot lives. Banner has its own background
        # so the text is never lost on a busy display.
        banner = self.ui_font.render(
            f"[{idx}/{total}] {status}", True, (255, 255, 255))
        bw, bh = banner.get_size()
        bx = (self.sw - bw) // 2
        by = 60
        pygame.draw.rect(
            self.screen, (40, 80, 140),
            pygame.Rect(bx - 24, by - 12, bw + 48, bh + 24),
            border_radius=12,
        )
        self.screen.blit(banner, (bx, by))

        # Sub-banner: instruction
        sub = self.ui_font.render(
            "按 ESC 退出", True, (180, 180, 200))
        self.screen.blit(sub, ((self.sw - sub.get_width()) // 2, by + bh + 30))

        # Pulsing target dot
        pulse = int(6 * abs(np.sin(time.time() * 3)))
        pygame.draw.circle(
            self.screen, (40, 120, 200), point,
            POINT_RADIUS + 14 + pulse, width=4)
        pygame.draw.circle(self.screen, (255, 255, 255), point, POINT_RADIUS)
        pygame.draw.circle(self.screen, (255, 80, 80), point, 6)

        # Per-dot label so the user is sure where to click
        label = self.ui_font.render(f"#{idx}", True, (255, 255, 255))
        lx = point[0] - label.get_width() // 2
        ly = point[1] + POINT_RADIUS + 18
        if ly + label.get_height() > self.sh - 20:
            ly = point[1] - POINT_RADIUS - 18 - label.get_height()
        self.screen.blit(label, (lx, ly))

        pygame.display.flip()

    def calibrate(self):
        print("点击式 13 点校准。请注视每个圆点并点击它。")
        X, Y = [], []
        clock = pygame.time.Clock()

        for idx, point in enumerate(self.calibration_points, start=1):
            # Wait for the user to click on the highlighted dot
            clicked = False
            while not clicked:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        raise KeyboardInterrupt
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        mx, my = pygame.mouse.get_pos()
                        if np.hypot(mx - point[0], my - point[1]) <= POINT_RADIUS + 15:
                            clicked = True
                self._draw_cali_point(point, idx, len(self.calibration_points),
                                      "请点击圆点")
                clock.tick(TARGET_FPS)

            # Sample camera frames after the click
            valid_feats = []
            failed_reads = 0
            t_start = time.time()
            while (len(valid_feats) < CALI_SAMPLES_PER_POINT
                   and (time.time() - t_start) < 3.0):
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        raise KeyboardInterrupt
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt

                ok, frame = self.cap.read()
                if not ok:
                    failed_reads += 1
                    # If the camera silently fails for 30 consecutive frames
                    # bail out — better than dragging the user through 13
                    # impossible-to-pass calibration points.
                    if failed_reads > 30:
                        raise RuntimeError(
                            "camera_unavailable: cv2.VideoCapture.read() "
                            "returned False repeatedly during calibration. "
                            "The camera was opened but is no longer "
                            "delivering frames. Quit and re-grant camera "
                            "permission in System Settings → Privacy & "
                            "Security → Camera."
                        )
                    continue

                ff = self._extract(frame)
                if ff is not None and ff.quality > 0.1:
                    valid_feats.append(ff.feat)

                n_valid = len(valid_feats)
                self._draw_cali_point(point, idx, len(self.calibration_points),
                                      f"采样中... {n_valid}/{CALI_SAMPLES_PER_POINT}")
                clock.tick(TARGET_FPS)

            if len(valid_feats) < CALI_MIN_VALID_PER_POINT:
                print(f"[警告] 校准点 {idx}: 有效样本不足 ({len(valid_feats)}), 已跳过")
                continue

            feat_med = np.median(np.stack(valid_feats, axis=0), axis=0)
            X.append(feat_med)
            Y.append(point)
            print(f"  校准点 {idx}: 已采集 {len(valid_feats)} 个样本")

        if len(X) < 5:
            raise RuntimeError("校准失败：有效校准点过少")

        X = np.asarray(X, dtype=np.float64)
        Y = np.asarray(Y, dtype=np.float64)
        self.regressor.fit(X, Y)
        self.calibrated = True
        pred = self.regressor.predict(X)
        residuals = np.sqrt(np.sum((pred - Y) ** 2, axis=1))
        print(f"校准完成。有效点数={len(X)}, 平均残差={residuals.mean():.1f}px, 最大={residuals.max():.1f}px")

    def _predict_screen_point(self, feat: np.ndarray) -> Tuple[int, int]:
        xy = self.regressor.predict(feat[None, :].astype(np.float64))[0]
        x = float(np.clip(xy[0], 0, self.sw - 1))
        y = float(np.clip(xy[1], 0, self.sh - 1))
        return int(x), int(y)

    def _classify_zone(self, px: float, py: float) -> str:
        cx, cy = self.sw / 2.0, self.sh / 2.0
        dx, dy = px - cx, py - cy
        margin_x = self.sw * 0.18
        margin_y = self.sh * 0.18
        if abs(dx) < margin_x and abs(dy) < margin_y:
            return "center"
        if abs(dx) > abs(dy):
            return "right" if dx > 0 else "left"
        return "down" if dy > 0 else "up"

    def _draw_validation_point(self, vp, label, idx, total):
        """Render one frame of the calibration-validation phase.

        Mirrors the layout of ``_draw_cali_point``: a centred top banner so
        the user always sees status text regardless of where the validation
        target is on screen, plus the target circle and a per-target label.
        """
        # Plain 3-tuple fill — see fix history in `_draw_cali_point`.
        self.screen.fill((20, 20, 30))

        banner_text = f"[{idx}/{total}] 校准验证 — 请注视: {label}"
        banner = self.ui_font.render(banner_text, True, (255, 255, 255))
        bw, bh = banner.get_size()
        bx = (self.sw - bw) // 2
        by = 60
        pygame.draw.rect(
            self.screen, (40, 80, 140),
            pygame.Rect(bx - 24, by - 12, bw + 48, bh + 24),
            border_radius=12,
        )
        self.screen.blit(banner, (bx, by))

        sub = self.ui_font.render("无需点击 — 按 ESC 退出", True, (180, 180, 200))
        self.screen.blit(sub, ((self.sw - sub.get_width()) // 2, by + bh + 30))

        # The target circle (yellow ring + white dot)
        pygame.draw.circle(self.screen, (255, 200, 50), vp, POINT_RADIUS + 8,
                           width=4)
        pygame.draw.circle(self.screen, (255, 255, 255), vp, POINT_RADIUS - 4)
        pygame.draw.circle(self.screen, (255, 100, 50), vp, 6)

        # Big direction label next to the target
        big = self.ui_font.render(label, True, (255, 220, 80))
        lx = vp[0] - big.get_width() // 2
        ly = vp[1] + POINT_RADIUS + 18
        if ly + big.get_height() > self.sh - 20:
            ly = vp[1] - POINT_RADIUS - 18 - big.get_height()
        self.screen.blit(big, (lx, ly))

        pygame.display.flip()

    def _validate_calibration(self):
        """Post-calibration validation: show 5 cardinal targets for 2 s each
        and report mean prediction error for diagnostics. The user does not
        need to click — just look at each target.
        """
        verify_pts = [
            (int(self.sw * 0.5), int(self.sh * 0.1)),   # up
            (int(self.sw * 0.5), int(self.sh * 0.9)),   # down
            (int(self.sw * 0.1), int(self.sh * 0.5)),   # left
            (int(self.sw * 0.9), int(self.sh * 0.5)),   # right
            (int(self.sw * 0.5), int(self.sh * 0.5)),   # center
        ]
        labels = ["上", "下", "左", "右", "中"]
        clock = pygame.time.Clock()
        total = len(verify_pts)
        print(f"校准验证：请注视每个目标点 2 秒... ({total} 个点)")

        for idx, (vp, label) in enumerate(zip(verify_pts, labels), start=1):
            t_start = time.time()
            errors: list[float] = []
            while time.time() - t_start < 2.0:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return
                    if event.type == pygame.KEYDOWN \
                            and event.key == pygame.K_ESCAPE:
                        raise KeyboardInterrupt
                ok, frame = self.cap.read()
                if not ok:
                    continue
                ff = self._extract(frame)
                if ff is not None:
                    self.feat_buf.append(ff.feat)
                if len(self.feat_buf) >= 3:
                    feat = self._weighted_mean_feat()
                    mx, my = self._predict_screen_point(feat)
                    err = np.hypot(mx - vp[0], my - vp[1])
                    errors.append(err)

                self._draw_validation_point(vp, label, idx, total)
                clock.tick(TARGET_FPS)

            if errors:
                mean_err = np.mean(errors)
                print(f"  {label}: 平均误差 = {mean_err:.0f}px")
            else:
                print(f"  {label}: 无有效样本")

        self.feat_buf.clear()
        self.smoother = FixationSmoother()
        print("验证完成。开始追踪...")

    def _weighted_mean_feat(self) -> np.ndarray:
        """指数加权平均：越新的帧权重越大。"""
        arr = np.stack(self.feat_buf, axis=0)
        n = len(arr)
        weights = np.exp(np.linspace(-1.0, 0.0, n))
        weights /= weights.sum()
        return np.average(arr, axis=0, weights=weights)

    def track(self):
        print("追踪已开始。关闭窗口停止。")
        print(f"被试: {self.subject_id} | 视频: {self.video_name}")
        t0 = time.time()
        clock = pygame.time.Clock()
        self.raw_gaze_log.clear()
        self.timestamp_log.clear()

        while True:
            elapsed = time.time() - t0
            if elapsed > TRACKING_DURATION_SEC:
                break

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    return

            # === 视频刺激播放 ===
            video_frame_surface = None
            if self.video_cap is not None:
                vok, vframe = self.video_cap.read()
                if not vok:
                    # 视频结束 → 停止追踪
                    print("视频播放结束")
                    break
                vframe = cv2.resize(vframe, (self.sw, self.sh))
                vframe_rgb = cv2.cvtColor(vframe, cv2.COLOR_BGR2RGB)
                video_frame_surface = pygame.surfarray.make_surface(
                    np.transpose(vframe_rgb, (1, 0, 2))
                )

            ok, frame = self.cap.read()
            if not ok:
                # 记录当前帧为 NaN (丢失)
                self.raw_gaze_log.append(np.array([np.nan, np.nan]))
                self.timestamp_log.append(elapsed)
                continue

            ff = self._extract(frame)
            if ff is not None:
                self.feat_buf.append(ff.feat)

            point = None
            if len(self.feat_buf) >= 3:
                feat = self._weighted_mean_feat()
                mx, my = self._predict_screen_point(feat)

                # 注视感知自适应平滑
                sx, sy = self.smoother.update(float(mx), float(my))

                px = int(np.clip(sx, 0, self.sw - 1))
                py = int(np.clip(sy, 0, self.sh - 1))
                point = (px, py)
                self.tracked_points.append(point)
                self.gaze_zone = self._classify_zone(px, py)

                # ===== 记录原始注视坐标 (ECML 格式) =====
                self.raw_gaze_log.append(np.array([float(px), float(py)]))
                self._publish_gaze(
                    float(px), float(py),
                    ff.pupil_proxy if ff is not None else float("nan"),
                    True,
                )
            else:
                # 当前帧无有效注视 → NaN
                self.raw_gaze_log.append(np.array([np.nan, np.nan]))
                self._publish_gaze(float("nan"), float("nan"), float("nan"), False)

            self.timestamp_log.append(elapsed)

            # 绘制
            if video_frame_surface is not None:
                self.screen.blit(video_frame_surface, (0, 0))
            else:
                # Plain 3-tuple — see fix history in `_draw_cali_point`.
                # The 4-tuple form is a no-op on macOS Metal but causes
                # screen flicker / blank frames on some setups.
                self.screen.fill((20, 20, 30))

            if point is not None:
                pygame.draw.circle(self.screen, (80, 200, 255), point, 14)
                pygame.draw.circle(self.screen, (255, 255, 255), point, 5)
                zone_colors = {
                    "center": (100, 255, 100),
                    "up": (255, 255, 100),
                    "down": (255, 150, 50),
                    "left": (100, 200, 255),
                    "right": (200, 100, 255),
                }
                color = zone_colors.get(self.gaze_zone, (200, 200, 200))
                zone_text = self.ui_font.render(f"方位: {self.gaze_zone.upper()}", True, color)
                self.screen.blit(zone_text, (20, 20))

            # 录制状态提示
            rec_text = self.ui_font.render(
                f"REC  {self.subject_id}  {int(elapsed)}s  samples={len(self.raw_gaze_log)}",
                True, (255, 60, 60),
            )
            self.screen.blit(rec_text, (self.sw - rec_text.get_width() - 20, 20))

            pygame.display.flip()
            clock.tick(TARGET_FPS)

    def save_heatmap(self):
        """Generate a heatmap overlay over a black background.

        The original implementation used ``PIL.ImageGrab.grab()`` to overlay
        the heatmap on the current desktop screenshot — this is Windows-only
        (and on macOS requires Screen Recording permission). For cross-platform
        portability we render against a plain black background.
        """
        bg = np.zeros((self.sh, self.sw, 3), dtype=np.uint8)
        heat = np.zeros((self.sh, self.sw), dtype=np.float32)

        for px, py in self.tracked_points:
            heat[py, px] += 1.0

        if np.count_nonzero(heat) > 0:
            heat = cv2.GaussianBlur(heat, (HEATMAP_BLUR_KSIZE, HEATMAP_BLUR_KSIZE), 0)
            heat = heat / (heat.max() + 1e-6)
        heat_u8 = np.clip(heat * 255, 0, 255).astype(np.uint8)
        heat_color = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)
        overlay = cv2.addWeighted(bg, 0.72, heat_color, 0.28, 0)

        ECML_HEATMAP_DIR.mkdir(parents=True, exist_ok=True)
        save_path = ECML_HEATMAP_DIR / f"heatmap_{self.subject_id}_Video_{self.video_name}.png"
        cv2.imwrite(str(save_path), overlay)
        print(f"热力图已保存: {save_path}")

    # ═══════════════════════  ECML-HBN 数据输出  ═══════════════════════
    def save_ecml_data(self) -> str:
        """Persist recorded gaze data in ECML-HBN compatible format."""
        ECML_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ECML_TIMESTAMPS_DIR.mkdir(parents=True, exist_ok=True)
        ECML_METADATA_DIR.mkdir(parents=True, exist_ok=True)

        gaze_array = np.array(self.raw_gaze_log, dtype=np.float64)  # (T, 2)
        ts_array = np.array(self.timestamp_log, dtype=np.float64)   # (T,)

        key = f"{self.subject_id}_Video_{self.video_name}"

        npy_path = ECML_OUTPUT_DIR / f"X_px_{key}.npy"
        ts_path = ECML_TIMESTAMPS_DIR / f"timestamps_{key}.npy"
        meta_path = ECML_METADATA_DIR / f"metadata_{key}.json"

        np.save(npy_path, gaze_array)
        np.save(ts_path, ts_array)

        # 数据质量统计
        valid_ratio = float(np.isfinite(gaze_array[:, 0]).mean()) if len(gaze_array) > 0 else 0.0
        actual_fps = len(gaze_array) / max(1e-6, ts_array[-1] - ts_array[0]) if len(ts_array) > 1 else 0.0

        import json
        metadata = {
            "subject_id": self.subject_id,
            "video_name": self.video_name,
            "screen_width": self.sw,
            "screen_height": self.sh,
            "total_samples": len(gaze_array),
            "duration_sec": float(ts_array[-1]) if len(ts_array) > 0 else 0,
            "valid_ratio": valid_ratio,
            "actual_fps": actual_fps,
            "target_fps": TARGET_FPS,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "format": "ECML-HBN compatible — X_px shape (T, 2)",
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        print("\n===== ECML 数据已保存 =====")
        print(f"  注视序列: {npy_path}")
        print(f"  时间戳:   {ts_path}")
        print(f"  元数据:   {meta_path}")
        print(f"  总采样:   {len(gaze_array)}, 有效率: {valid_ratio:.1%}, 实际FPS: {actual_fps:.1f}")
        return str(npy_path)

    def run_sternberg_screening(self) -> dict:
        """
        运行 Sternberg 视觉空间工作记忆任务 + ADHD 预测。

        流程:
          1. 执行 160 trial 的 Sternberg 范式 (与 Pupil_dataset 相同)
          2. 记录行为数据 (RT, accuracy) + 注视/瞳孔时间序列
          3. 提取 27 个特征 (与训练集特征工程完全一致)
          4. 调用保存的随机森林模型进行 ADHD vs Control 预测

        Returns: 筛查结果 dict
        """
        from adhd_engine.worker.sternberg import SternbergTask
        from adhd_engine.worker.inference import extract_features, predict_adhd

        # 1. Run Sternberg task
        task = SternbergTask(self, ipc_publisher=self.ipc_publisher,
                             session_id=self.session_id)
        trial_results = task.run()

        # 2. Save behavioural CSV under per-user data dir
        output_dir = engine_config.USER_DATA_DIR / "task_data"
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / f"behavioral_{self.subject_id}.csv"
        task.save_behavioral_csv(str(csv_path))

        # 3. Feature extraction (slope unit fix applied — see plan §1.5.1)
        print("\n  正在提取特征...")
        features = extract_features(trial_results, fps=TARGET_FPS)
        print(f"  已提取 {len(features)} 个特征")

        # 4. RF prediction (model dir handled by inference.predict_adhd)
        rf_path = engine_config.SAVED_MODELS_DIR / "random_forest.joblib"
        if not rf_path.is_file():
            print(f"[错误] 未找到模型: {rf_path}")
            return {}

        result = predict_adhd(features)

        # 5. 打印结果
        print("\n" + "=" * 50)
        print("  ADHD 筛查结果")
        print("=" * 50)
        print(f"  被试:       {self.subject_id}")
        print(f"  预测:       {result['prediction']}")
        print(f"  ADHD概率:   {result['adhd_probability']:.1%}")
        print(f"  风险等级:   {result['risk_level']}")
        print(f"  模型:       {result['model_info']}")
        print(f"\n  使用的关键特征:")
        for name, val in result['feature_values'].items():
            imp = result['feature_importance'].get(name, 0)
            print(f"    {name:<30} = {val:>8.4f}  (imp={imp:.3f})")
        print("=" * 50)

        # 6. Persist report JSON to per-user task_data dir
        import json
        report_path = output_dir / f"adhd_report_{self.subject_id}.json"
        report = {
            'subject_id': self.subject_id,
            'session_id': self.session_id,
            **result,
            'all_features': features,
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"  报告已保存: {report_path}")

        return result

    def close(self):
        if self.cap.isOpened():
            self.cap.release()
        if self.video_cap is not None and self.video_cap.isOpened():
            self.video_cap.release()
        self.face_mesh.close()
        pygame.quit()
        cv2.destroyAllWindows()


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="眼动追踪器 — ADHD 筛查 (Sternberg 任务)")
    parser.add_argument("--subject", default=SUBJECT_ID, help="被试编号")
    parser.add_argument(
        "--mode", choices=["sternberg", "track"], default="sternberg",
        help="sternberg: 运行 Sternberg WM 任务 + ADHD 预测; "
             "track: 自由注视追踪模式")
    parser.add_argument(
        "--video", default=VIDEO_STIMULUS_PATH,
        help="(track 模式) 视频刺激路径")
    args = parser.parse_args()

    system = GazeSystem(subject_id=args.subject, video_path=args.video)
    try:
        system.calibrate()
        system._validate_calibration()

        if args.mode == "sternberg":
            # ===== Sternberg 任务 + ADHD 筛查 =====
            result = system.run_sternberg_screening()
        else:
            # ===== 自由追踪模式 =====
            system.track()
            system.save_heatmap()
            system.save_ecml_data()
    finally:
        system.close()
        print("程序结束。")


if __name__ == "__main__":
    main()
