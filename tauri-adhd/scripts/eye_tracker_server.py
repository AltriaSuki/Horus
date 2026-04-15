"""
眼动追踪 TCP 服务器 v4  —  极速 SVM + 纯虹膜特征
=================================================
完全跳过模型推理, 每帧 < 10 ms

Pipeline:
  MediaPipe FaceMesh → 虹膜比率(4D) + 头部姿态(2D) = 6D
  → SVM(RBF) 方向分类 → 3 帧多数投票 → zone
  → Ridge 回归 → 平滑 → gaze_x / gaze_y

JSON:
  {"zone":"up","gaze_x":0.42,"gaze_y":0.11,"ts":1712345678.12}

启动:
  python eye_tracker_server.py              # TCP 5678
  python eye_tracker_server.py --port 9999
  python eye_tracker_server.py --test       # 本地测试窗口
"""

import os, sys, time, ctypes, json, socket, threading, argparse, traceback
from collections import deque, Counter

import cv2
import numpy as np
import pygame
from sklearn.svm import SVC
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score

try:
    from google.protobuf import message_factory as _mf
    if not hasattr(_mf.MessageFactory, "GetPrototype"):
        _mf.MessageFactory.GetPrototype = lambda self, d: _mf.GetMessageClass(d)
except Exception:
    pass

import mediapipe as mp

# ==================== 配 置 ====================

CAMERA_ID       = 0
FPS             = 40
VOTE_WINDOW     = 5        # 多数投票窗口 (帧)
FAST_SWITCH_STREAK = 2     # 连续命中后快速切换方向
POS_SMOOTH_ALPHA = 0.4     # 越大越跟手
MAX_CAMERA_READ_FAILS = 15

CALI_SAMPLES    = 40       # 每个校准点采样数
CALI_MIN_OK     = 20       # 最少有效样本
CALI_RADIUS     = 22
EAR_THRESH      = 0.015     # 眨眼过滤

# 15 点校准: 每方向 3 点, 适度展开
CALI_GRID = [
    # center (3)
    (0.50, 0.50, "center"),
    (0.42, 0.45, "center"),
    (0.58, 0.55, "center"),
    # up (3)
    (0.35, 0.15, "up"),
    (0.50, 0.15, "up"),
    (0.65, 0.15, "up"),
    # down (3)
    (0.35, 0.90, "down"),
    (0.50, 0.90, "down"),
    (0.65, 0.90, "down"),
    # left (3)
    (0.10, 0.40, "left"),
    (0.10, 0.50, "left"),
    (0.10, 0.60, "left"),
    # right (3)
    (0.90, 0.40, "right"),
    (0.90, 0.50, "right"),
    (0.90, 0.60, "right"),
]

LEFT_EYE  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]


# ==================== 虹膜特征 ====================

def compute_ear(lm, idx):
    p = [np.array([lm[i].x, lm[i].y]) for i in idx]
    v = np.linalg.norm(p[1] - p[5]) + np.linalg.norm(p[2] - p[4])
    return float(v / (2 * max(1e-6, np.linalg.norm(p[0] - p[3]))))


def extract_iris(lm):
    """从 MediaPipe landmarks 提取 8D 特征:
    [iris_r_u, iris_r_n, iris_l_u, iris_l_n, yaw, pitch, r_vpos, l_vpos]
    """
    def ratio(c1, c2, ir):
        a = np.array([lm[c1].x, lm[c1].y])
        b = np.array([lm[c2].x, lm[c2].y])
        c = np.array([lm[ir].x, lm[ir].y])
        ax = b - a
        ln = np.linalg.norm(ax)
        if ln < 1e-6:
            return None
        u = ax / ln
        n = np.array([-u[1], u[0]])
        o = c - a
        return [np.dot(o, u) / ln, np.dot(o, n) / ln]

    r = ratio(33, 133, 468)
    l = ratio(362, 263, 473)
    if r is None or l is None:
        return None

    nose = np.array([lm[1].x, lm[1].y])
    chin = np.array([lm[152].x, lm[152].y])
    fhd  = np.array([lm[10].x, lm[10].y])
    lc   = np.array([lm[234].x, lm[234].y])
    rc   = np.array([lm[454].x, lm[454].y])
    fw = max(1e-6, abs(rc[0] - lc[0]))
    fh = max(1e-6, abs(chin[1] - fhd[1]))
    yaw   = (nose[0] - (lc[0] + rc[0]) / 2) / fw
    pitch = (nose[1] - (fhd[1] + chin[1]) / 2) / fh

    # 虹膜在上下眼睑之间的垂直位置 (0=紧贴上眼睑=向上, 1=紧贴下眼睑=向下)
    r_upper = np.mean([lm[158].y, lm[159].y, lm[160].y])
    r_lower = np.mean([lm[144].y, lm[145].y, lm[153].y])
    r_vpos = (lm[468].y - r_upper) / max(1e-6, r_lower - r_upper)
    l_upper = np.mean([lm[385].y, lm[386].y, lm[387].y])
    l_lower = np.mean([lm[373].y, lm[374].y, lm[380].y])
    l_vpos = (lm[473].y - l_upper) / max(1e-6, l_lower - l_upper)

    return np.array(r + l + [yaw, pitch, r_vpos, l_vpos], dtype=np.float32)


# ==================== 位置平滑 ====================

class PositionSmoother:
    def __init__(self, alpha=POS_SMOOTH_ALPHA):
        self.alpha = alpha
        self.x = self.y = None

    def update(self, x, y):
        if self.x is None:
            self.x, self.y = x, y
        else:
            self.x += self.alpha * (x - self.x)
            self.y += self.alpha * (y - self.y)
        return self.x, self.y

    def reset(self):
        self.x = self.y = None


# ==================== 眼动追踪核心 ====================

class EyeTrackerCore:
    """
    纯虹膜 6D 特征 → SVM 方向 + Ridge 位置
    无模型推理, 每帧 < 10 ms
    """

    def __init__(self):
        self.mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True,
            min_detection_confidence=0.6, min_tracking_confidence=0.6)
        self.cap = cv2.VideoCapture(CAMERA_ID)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, FPS)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.zone_clf = None       # SVM Pipeline
        self.pos_reg  = None       # Ridge Pipeline
        self.smoother = PositionSmoother()
        self.vote_q   = deque(maxlen=VOTE_WINDOW)
        self.stable_zone = "center"
        self.sw = self.sh = 0
        self.read_fail_count = 0

    def _reopen_camera(self):
        try:
            if self.cap.isOpened():
                self.cap.release()
        except Exception:
            pass
        self.cap = cv2.VideoCapture(CAMERA_ID)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, FPS)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.read_fail_count = 0

    def _get_feat(self, frame):
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = self.mesh.process(rgb)
            if not res.multi_face_landmarks:
                return None
            lm = res.multi_face_landmarks[0].landmark
            ear = 0.5 * (compute_ear(lm, RIGHT_EYE) + compute_ear(lm, LEFT_EYE))
            if ear < EAR_THRESH:
                return None
            return extract_iris(lm)
        except Exception as exc:
            print(f"[Tracker] frame processing error: {exc}")
            return None

    # ---------- 校准 ----------
    def calibrate(self):
        pygame.init(); pygame.font.init()
        info = pygame.display.Info()
        self.sw, self.sh = info.current_w, info.current_h
        scr = pygame.display.set_mode((self.sw, self.sh), pygame.NOFRAME)
        hwnd = pygame.display.get_wm_info()["window"]
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, self.sw, self.sh, 0x0001)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, 0x00080000)
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0x000000, 180, 0x2)
        font = pygame.font.Font(None, 28)
        clock = pygame.time.Clock()

        all_feats  = []
        all_labels = []
        all_pos    = []

        for idx, (xn, yn, label) in enumerate(CALI_GRID, 1):
            pt = (int(xn * self.sw), int(yn * self.sh))

            # 等待点击
            clicked = False
            while not clicked:
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        raise KeyboardInterrupt
                    if ev.type == pygame.MOUSEBUTTONDOWN:
                        if np.hypot(ev.pos[0] - pt[0], ev.pos[1] - pt[1]) <= CALI_RADIUS + 15:
                            clicked = True
                scr.fill((0, 0, 0, 0))
                pulse = int(4 * abs(np.sin(time.time() * 3)))
                pygame.draw.circle(scr, (40, 120, 200), pt, CALI_RADIUS + 10 + pulse)
                pygame.draw.circle(scr, (255, 255, 255), pt, CALI_RADIUS)
                pygame.draw.circle(scr, (255, 80, 80), pt, 5)
                txt = font.render(
                    f"[{idx}/{len(CALI_GRID)}] {label.upper()} - Click & look", True, (220, 220, 220))
                tx = max(10, min(pt[0] - txt.get_width() // 2, self.sw - txt.get_width() - 10))
                ty = pt[1] + CALI_RADIUS + 25
                if ty + txt.get_height() > self.sh - 10:
                    ty = pt[1] - CALI_RADIUS - 35
                scr.blit(txt, (tx, ty))
                pygame.display.flip()
                clock.tick(FPS)

            # 等 0.3s 让视线从鼠标移到校准点
            t_settle = time.time()
            while time.time() - t_settle < 0.3:
                for ev in pygame.event.get():
                    pass
                self.cap.read()
                scr.fill((0, 0, 0, 0))
                pygame.draw.circle(scr, (255, 200, 50), pt, CALI_RADIUS)
                pygame.draw.circle(scr, (255, 255, 255), pt, 8)
                scr.blit(font.render("Settling...", True, (255, 255, 150)),
                         (pt[0] - 40, pt[1] + CALI_RADIUS + 20))
                pygame.display.flip()
                clock.tick(FPS)

            # 采集样本
            feats = []
            t0 = time.time()
            while len(feats) < CALI_SAMPLES and time.time() - t0 < 3:
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        raise KeyboardInterrupt
                ok, frame = self.cap.read()
                if ok:
                    f = self._get_feat(frame)
                    if f is not None:
                        feats.append(f)
                scr.fill((0, 0, 0, 0))
                pygame.draw.circle(scr, (40, 200, 100), pt, CALI_RADIUS)
                pygame.draw.circle(scr, (255, 255, 255), pt, 8)
                n_txt = font.render(f"Sampling {len(feats)}/{CALI_SAMPLES}", True, (200, 255, 200))
                scr.blit(n_txt, (pt[0] - 60, pt[1] + CALI_RADIUS + 20))
                pygame.display.flip()
                clock.tick(FPS)

            if len(feats) < CALI_MIN_OK:
                print(f"  point {idx} ({label}): too few ({len(feats)}), skip")
                continue

            all_feats.extend(feats)
            all_labels.extend([label] * len(feats))
            all_pos.extend([pt] * len(feats))
            print(f"  point {idx} ({label}): {len(feats)} samples")

        if len(set(all_labels)) < 5:
            pygame.quit()
            raise RuntimeError("Calibration failed: missing directions")

        X = np.array(all_feats, dtype=np.float64)
        y = np.array(all_labels)
        Y_pos = np.array(all_pos, dtype=np.float64)

        # 诊断
        scaler_dbg = StandardScaler().fit(X)
        Xs = scaler_dbg.transform(X)
        feat_names = ['ir_r_u', 'ir_r_n', 'ir_l_u', 'ir_l_n', 'yaw', 'pitch', 'r_vpos', 'l_vpos']
        print("[Diag] Per-class standardized feature means:")
        for zone in ['center', 'up', 'down', 'left', 'right']:
            mask = y == zone
            if mask.any():
                means = Xs[mask].mean(axis=0)
                vals = ' '.join(f"{n}:{m:+.2f}" for n, m in zip(feat_names, means))
                print(f"  {zone:>6s}: {vals}")

        # SVM
        try:
            cv_pipe = Pipeline([
                ('s', StandardScaler()),
                ('svm', SVC(kernel='rbf', C=10, gamma='scale', class_weight='balanced'))])
            scores = cross_val_score(cv_pipe, X, y, cv=5, scoring='accuracy')
            print(f"[SVM] 5-fold CV: {scores.mean()*100:.1f}% +/- {scores.std()*100:.1f}%")
        except Exception:
            print("[SVM] CV skipped")

        self.zone_clf = Pipeline([
            ('s', StandardScaler()),
            ('svm', SVC(kernel='rbf', C=10, gamma='scale', class_weight='balanced'))])
        self.zone_clf.fit(X, y)
        print(f"[SVM] Train accuracy: {self.zone_clf.score(X, y)*100:.1f}%  ({len(X)} samples)")

        # Ridge 位置回归
        self.pos_reg = Pipeline([('s', StandardScaler()), ('r', Ridge(alpha=1.0))])
        self.pos_reg.fit(X, Y_pos)

        # 5 方向验证
        verify = [
            (int(self.sw * 0.5), int(self.sh * 0.1), "UP"),
            (int(self.sw * 0.5), int(self.sh * 0.9), "DOWN"),
            (int(self.sw * 0.1), int(self.sh * 0.5), "LEFT"),
            (int(self.sw * 0.9), int(self.sh * 0.5), "RIGHT"),
            (int(self.sw * 0.5), int(self.sh * 0.5), "CENTER"),
        ]
        print("Validation: look at 5 targets for 2s each...")
        for vx, vy, label in verify:
            t_start = time.time()
            preds = []
            while time.time() - t_start < 2.0:
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        break
                ok, frame = self.cap.read()
                if ok:
                    f = self._get_feat(frame)
                    if f is not None:
                        preds.append(self.zone_clf.predict(f.reshape(1, -1))[0])
                scr.fill((0, 0, 0, 0))
                pygame.draw.circle(scr, (255, 200, 50), (vx, vy), CALI_RADIUS + 6)
                pygame.draw.circle(scr, (255, 255, 255), (vx, vy), CALI_RADIUS - 4)
                txt = font.render(f"Look: {label}", True, (255, 255, 100))
                scr.blit(txt, (self.sw // 2 - 60, int(self.sh * 0.04)))
                pygame.display.flip()
                clock.tick(FPS)
            if preds:
                votes = Counter(preds)
                mc = votes.most_common(1)[0]
                mark = "OK" if label.lower() == mc[0] else "MISS"
                print(f"  {label}: {mc[0].upper()} ({mc[1]/len(preds)*100:.0f}%) [{mark}]")
            else:
                print(f"  {label}: no valid samples")

        self.vote_q.clear()
        self.smoother.reset()
        self.stable_zone = "center"
        pygame.quit()
        print("Calibration done.\n")

    # ---------- 实时处理 ----------
    def process_frame(self):
        """每帧: 6D特征 → SVM分类 → 投票 → (zone, gx, gy)"""
        ok, frame = self.cap.read()
        if not ok:
            self.read_fail_count += 1
            if self.read_fail_count >= MAX_CAMERA_READ_FAILS:
                print("[Tracker] camera read failed repeatedly, reopening...")
                self._reopen_camera()
            return None

        self.read_fail_count = 0

        gx, gy = 0.5, 0.5

        try:
            feat = self._get_feat(frame)

            if feat is not None and self.zone_clf is not None:
                # 方向: 单帧 SVM 分类 → 投票
                zone = self.zone_clf.predict(feat.reshape(1, -1))[0]
                self.vote_q.append(zone)

                recent = list(self.vote_q)[-FAST_SWITCH_STREAK:]
                if (
                    len(recent) == FAST_SWITCH_STREAK
                    and len(set(recent)) == 1
                    and recent[-1] != self.stable_zone
                ):
                    self.stable_zone = recent[-1]

                # 位置: Ridge → 平滑
                if self.pos_reg is not None:
                    pos = self.pos_reg.predict(feat.reshape(1, -1))[0]
                    rx = float(np.clip(pos[0], 0, self.sw - 1))
                    ry = float(np.clip(pos[1], 0, self.sh - 1))
                    sx, sy = self.smoother.update(rx, ry)
                    gx = float(np.clip(sx / self.sw, 0, 1))
                    gy = float(np.clip(sy / self.sh, 0, 1))

            # 多数投票
            if self.vote_q:
                counts = Counter(self.vote_q)
                mc = counts.most_common()
                if len(mc) >= 2 and mc[0][1] == mc[1][1]:
                    self.stable_zone = self.vote_q[-1]   # 平票 → 取最新
                else:
                    self.stable_zone = mc[0][0]
        except Exception as exc:
            print(f"[Tracker] process_frame error: {exc}")

        return (self.stable_zone, gx, gy)

    def close(self):
        if self.cap.isOpened():
            self.cap.release()
        self.mesh.close()


# ==================== TCP 服务器 ====================

class GazeServer:
    def __init__(self, tracker: EyeTrackerCore, host="127.0.0.1", port=5678):
        self.tracker = tracker
        self.host = host
        self.port = port
        self.running = False
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()

    def start(self):
        self.running = True
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(2)
        srv.settimeout(0.5)
        print(f"[Server] Listening on {self.host}:{self.port}")

        accept_t = threading.Thread(target=self._accept_loop, args=(srv,), daemon=True)
        accept_t.start()

        frame_interval = 1.0 / FPS
        try:
            while self.running:
                t_start = time.time()
                result = self.tracker.process_frame()
                if result is None:
                    continue
                zone, gx, gy = result
                msg = json.dumps({
                    "zone": zone,
                    "gaze_x": round(gx, 4),
                    "gaze_y": round(gy, 4),
                    "ts": round(time.time(), 3),
                }) + "\n"
                self._broadcast(msg)
                # Throttle to target FPS
                elapsed = time.time() - t_start
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        except KeyboardInterrupt:
            print("\n[Server] Interrupted.")
        finally:
            self.running = False
            srv.close()
            with self.lock:
                for c in self.clients:
                    try:
                        c.close()
                    except Exception:
                        pass
            print("[Server] Stopped.")

    def _accept_loop(self, srv):
        while self.running:
            try:
                conn, addr = srv.accept()
                with self.lock:
                    self.clients.append(conn)
                print(f"[Server] Client connected: {addr}")
            except socket.timeout:
                continue
            except OSError:
                break

    def _broadcast(self, msg):
        data = msg.encode("utf-8")
        dead = []
        with self.lock:
            for c in self.clients:
                try:
                    c.sendall(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    dead.append(c)
            for c in dead:
                self.clients.remove(c)
                try:
                    c.close()
                except Exception:
                    pass
                print("[Server] Client disconnected.")


# ==================== 测试窗口 ====================

def run_test_window(tracker: EyeTrackerCore):
    pygame.init(); pygame.font.init()
    W, H = 640, 480
    scr = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Eye Tracker Test v4 (ESC to quit)")
    font = pygame.font.Font(None, 28)
    clock = pygame.time.Clock()
    colors = {
        "center": (120, 255, 120), "up": (255, 255, 80),
        "down": (255, 150, 50),    "left": (80, 180, 255), "right": (200, 100, 255)}

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                pygame.quit(); return

        result = tracker.process_frame()
        scr.fill((20, 20, 30))

        if result:
            zone, gx, gy = result
            px, py = int(gx * W), int(gy * H)
            color = colors.get(zone, (200, 200, 200))
            pygame.draw.line(scr, (40, 50, 40), (W // 2, 0), (W // 2, H))
            pygame.draw.line(scr, (40, 50, 40), (0, H // 2), (W, H // 2))
            pygame.draw.circle(scr, color, (px, py), 14)
            pygame.draw.circle(scr, (255, 255, 255), (px, py), 5)
            txt = font.render(f"Zone: {zone.upper()}  ({gx:.2f}, {gy:.2f})", True, color)
            scr.blit(txt, (15, 15))

        pygame.display.flip()
        clock.tick(FPS)


# ==================== Headless 模式 (从 stdin 接收校准命令) ====================

def run_headless(tracker: EyeTrackerCore, port: int):
    """
    Headless calibration mode — no pygame window.
    Commands via stdin (one JSON per line):
      COLLECT x_norm y_norm label   → collect samples at normalized coords
      TRAIN                         → train SVM/Ridge models
      SERVE                         → start TCP server

    Responses on stdout:
      READY                         → camera opened, waiting for commands
    POINT_PROGRESS index n_samples target_samples → collecting progress for one point
    POINT_DONE index n_samples target_samples    → finished collecting for one point
      TRAIN_DONE accuracy           → model trained successfully
      TRAIN_FAILED reason           → model training failed
      [Server] Listening on ...     → TCP server started
    """
    import pygame  # still needed for screen size detection
    pygame.init()
    info = pygame.display.Info()
    tracker.sw, tracker.sh = info.current_w, info.current_h
    pygame.quit()

    print("READY", flush=True)

    all_feats  = []
    all_labels = []
    all_pos    = []
    point_idx  = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        cmd = parts[0].upper()

        if cmd == "COLLECT" and len(parts) >= 4:
            xn = float(parts[1])
            yn = float(parts[2])
            label = parts[3]
            pt = (int(xn * tracker.sw), int(yn * tracker.sh))

            # Settle: match screening calibration timing (0.3s)
            t_settle = time.time()
            while time.time() - t_settle < 0.3:
                tracker.cap.read()

            # Collect in a fixed 3s window (same as screening calibration UI)
            # so the frontend progress ring can animate smoothly by time.
            headless_target_samples = CALI_MIN_OK
            headless_min_ok = CALI_MIN_OK
            feats = []
            t0 = time.time()
            while time.time() - t0 < 3.0:
                ok, frame = tracker.cap.read()
                if ok:
                    f = tracker._get_feat(frame)
                    if f is not None:
                        feats.append(f)
                        print(f"POINT_PROGRESS {point_idx} {len(feats)} {headless_target_samples}", flush=True)

            if len(feats) >= headless_min_ok:
                all_feats.extend(feats)
                all_labels.extend([label] * len(feats))
                all_pos.extend([pt] * len(feats))

            print(f"POINT_DONE {point_idx} {len(feats)} {headless_target_samples}", flush=True)
            point_idx += 1

        elif cmd == "TRAIN":
            if len(set(all_labels)) < 5:
                print("TRAIN_FAILED missing_directions", flush=True)
                continue

            X = np.array(all_feats, dtype=np.float64)
            y = np.array(all_labels)
            Y_pos = np.array(all_pos, dtype=np.float64)

            # SVM
            try:
                cv_pipe = Pipeline([
                    ('s', StandardScaler()),
                    ('svm', SVC(kernel='rbf', C=10, gamma='scale',
                                class_weight='balanced'))])
                scores = cross_val_score(cv_pipe, X, y, cv=5, scoring='accuracy')
                acc = scores.mean() * 100
                print(f"[SVM] 5-fold CV: {acc:.1f}% +/- {scores.std()*100:.1f}%")
            except Exception:
                acc = 0.0

            tracker.zone_clf = Pipeline([
                ('s', StandardScaler()),
                ('svm', SVC(kernel='rbf', C=10, gamma='scale',
                            class_weight='balanced'))])
            tracker.zone_clf.fit(X, y)
            train_acc = tracker.zone_clf.score(X, y) * 100
            print(f"[SVM] Train accuracy: {train_acc:.1f}%  ({len(X)} samples)")

            tracker.pos_reg = Pipeline([
                ('s', StandardScaler()), ('r', Ridge(alpha=1.0))])
            tracker.pos_reg.fit(X, Y_pos)

            tracker.vote_q.clear()
            tracker.smoother.reset()
            tracker.stable_zone = "center"

            print(f"TRAIN_DONE {train_acc:.1f}", flush=True)
            print("Calibration done.")

        elif cmd == "SERVE":
            server = GazeServer(tracker, port=port)
            server.start()
            break  # server.start() blocks, after it returns we exit

        else:
            print(f"[Headless] Unknown command: {line}", flush=True)


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(description="Eye Tracker TCP Server v4")
    parser.add_argument("--port", type=int, default=5678)
    parser.add_argument("--test", action="store_true", help="Open local test window")
    parser.add_argument("--headless", action="store_true",
                        help="Headless mode: calibration driven via stdin commands")
    args = parser.parse_args()

    tracker = EyeTrackerCore()
    try:
        if args.headless:
            run_headless(tracker, args.port)
        else:
            tracker.calibrate()
            time.sleep(0.3)
            if args.test:
                print("[Server] Test mode.")
                run_test_window(tracker)
            else:
                server = GazeServer(tracker, port=args.port)
                server.start()
    except Exception:
        print("[Fatal] eye_tracker_server crashed:")
        traceback.print_exc()
    finally:
        tracker.close()
        print("Done.")


if __name__ == "__main__":
    main()
