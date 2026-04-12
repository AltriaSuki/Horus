# Tauri 迁移计划：Rust 后端 + Web UI

## 一、目标

把 ADHD 早筛系统从 Python + Flutter + pygame 迁移到 **Tauri (Rust + Web)**。

| 维度 | 当前 | 目标 |
|---|---|---|
| 分发体积 | ~2 GB (venv + Unity + Flutter) | ~20-30 MB (Tauri binary) + Unity 游戏独立下载 |
| 安装步骤 | `python start.py` + 等 venv + 等 Flutter | 双击 `.exe` / `.app` |
| 启动速度 | 5-10 秒冷启动 | <1 秒 |
| 帧率 | 12-18 fps (Python CPU) | 30+ fps (ONNX Runtime native) |
| 跨平台 | Win + Mac | Win + Mac + Linux (Tauri 原生支持) |
| Android | 不支持 | Tauri Mobile (未来, beta) |
| 技术栈 | Python + Dart + pygame | Rust + HTML/CSS/JS |
| 依赖 | Python 3.12 + pip + venv | 无 (self-contained binary) |

---

## 二、架构图

```
┌────────────────────────────────────────────────────────────────────┐
│  Tauri 应用 (单个 .exe / .app, ~20-30 MB)                          │
│                                                                     │
│  ┌─── Rust 后端 (src-tauri/) ─────────────────────────────────┐    │
│  │                                                             │    │
│  │  ┌─ Camera Thread ────────────────────────────────┐        │    │
│  │  │  nokhwa / opencv 摄像头 640x480 @ 30fps        │        │    │
│  │  │  → face_mesh.onnx (MediaPipe, ort crate)       │        │    │
│  │  │  → iris features 6D + head pose 2D             │        │    │
│  │  │  → affnet.onnx (AFFNet, ort crate)              │        │    │
│  │  │  → Ridge regression (nalgebra)                   │        │    │
│  │  │  → FixationSmoother                              │        │    │
│  │  │  → pupil_proxy + 5-frame smooth                 │        │    │
│  │  │  结果通过 Tauri event 推给前端                    │        │    │
│  │  └────────────────────────────────────────────────┘        │    │
│  │                                                             │    │
│  │  ┌─ Tauri Commands ──────────────────────────────┐         │    │
│  │  │  create_subject(id, name, sex)                 │         │    │
│  │  │  start_session(subject_id, mode)               │         │    │
│  │  │  cancel_session(session_id)                    │         │    │
│  │  │  get_report(session_id) → JSON                 │         │    │
│  │  │  start_calibration() / submit_calibration_click│         │    │
│  │  │  launch_unity_game(game_name)                  │         │    │
│  │  └────────────────────────────────────────────────┘         │    │
│  │                                                             │    │
│  │  ┌─ 推理 ────────────────────────────────────────┐         │    │
│  │  │  extract_features(trial_data) → 27D            │         │    │
│  │  │  predict_adhd(features) → RF JSON 遍历         │         │    │
│  │  │  StandardScaler (硬编码 μ/σ from joblib)       │         │    │
│  │  └────────────────────────────────────────────────┘         │    │
│  │                                                             │    │
│  │  SQLite (rusqlite): subjects / sessions / trials / reports  │    │
│  │                                                             │    │
│  │  Unity 子进程: spawn .exe, stdin/stdout JSON gaze stream    │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ┌─── Web 前端 (src/) ────────────────────────────────────────┐    │
│  │                                                             │    │
│  │  HTML / CSS / JS (Svelte 或 vanilla)                        │    │
│  │                                                             │    │
│  │  页面:                                                      │    │
│  │    被试管理 / 早筛启动 / 早筛进度 / 结果报告 / 训练 / 历史  │    │
│  │                                                             │    │
│  │  Canvas:                                                    │    │
│  │    13 点校准 (用户点击 → invoke submit_calibration_click)    │    │
│  │    5 点验证 (只看, 不点)                                    │    │
│  │    Sternberg 试次 (fixation/encoding/distractor/probe/feedback)│  │
│  │    → requestAnimationFrame 精确帧控制                       │    │
│  │                                                             │    │
│  │  Tauri Events 监听:                                         │    │
│  │    on('gaze_frame', {x, y, pupil, valid}) → 试次数据记录    │    │
│  │    on('calibration_done', {error_px})                       │    │
│  │    on('session_status', {status})                           │    │
│  │                                                             │    │
│  │  主题: 柚子橘 #FF8C42 / 清新蓝绿 #4ECDC4 / 乳黄 #FFD166   │    │
│  │  字体: Noto Sans SC (subset, ~3 MB, 内嵌)                   │    │
│  │  游戏: 调 invoke('launch_unity_game', {name})               │    │
│  └─────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 三、项目结构

```
tauri-adhd/
├── src-tauri/                       ← Rust 后端
│   ├── Cargo.toml
│   ├── tauri.conf.json              ← Tauri 配置 (窗口大小/标题/图标)
│   ├── build.rs                     ← 编译时嵌入 ONNX 模型
│   ├── icons/                       ← 应用图标 (Win .ico / Mac .icns)
│   ├── models/                      ← ONNX 模型文件 (编译时嵌入 binary)
│   │   ├── face_detector.onnx       ← BlazeFace 人脸检测 (~1 MB)
│   │   ├── face_mesh.onnx           ← MediaPipe FaceMesh 478 点 (~3 MB)
│   │   ├── affnet.onnx              ← AFFNet 注视估计 (~7 MB)
│   │   └── rf_model.json            ← Random Forest 决策树 (~0.5 MB)
│   │
│   └── src/
│       ├── main.rs                  ← Tauri app 入口
│       ├── lib.rs                   ← module 声明
│       │
│       ├── camera.rs                ← 摄像头采集 (nokhwa crate)
│       │                              struct CameraCapture
│       │                              fn start() → JoinHandle
│       │                              fn latest_frame() → Mat
│       │
│       ├── face_mesh.rs             ← MediaPipe ONNX 推理
│       │                              struct FaceMeshDetector
│       │                              fn detect(frame) → Option<Landmarks478>
│       │                              fn compute_iris_features(lm) → [f32; 6]
│       │                              fn estimate_pupil_size(lm) → f32
│       │                              fn compute_ear(lm) → f32
│       │
│       ├── gaze.rs                  ← AFFNet + Ridge + Smoother
│       │                              struct GazeEstimator
│       │                              fn predict_gaze(eyes, face, rect) → [f32; 2]
│       │                              struct RidgeRegressor
│       │                              fn fit(X, Y)  /  fn predict(x) → (f32, f32)
│       │                              struct FixationSmoother
│       │
│       ├── pipeline.rs              ← 完整的每帧流水线
│       │                              struct GazePipeline
│       │                              fn process_frame(frame) → GazeFrame
│       │                              fn start_background() → 启动 camera+gaze 线程
│       │
│       ├── sternberg.rs             ← Sternberg 范式的数据层
│       │                              struct Trial / TrialResult
│       │                              struct SternbergSession
│       │                              fn generate_trials(config) → Vec<Trial>
│       │                              fn generate_distractor_sequence(...)
│       │                              (注: 渲染在 Web 前端, Rust 只管数据+时序)
│       │
│       ├── inference.rs             ← 27 特征提取 + RF 预测
│       │                              fn extract_features(trials, fps) → Features27
│       │                              fn predict_adhd(features) → AdhdReport
│       │                              struct RandomForest (from JSON)
│       │
│       ├── storage.rs               ← SQLite ORM (rusqlite)
│       │                              create_subject / create_session / ...
│       │                              save_report / get_report
│       │
│       ├── game.rs                  ← Unity 子进程管理
│       │                              fn launch_game(exe_path, game_name)
│       │                              fn send_gaze(stdin, frame)
│       │                              fn stop_game(pid)
│       │
│       └── commands.rs              ← Tauri #[command] 函数
│                                      所有前端能调的 IPC 接口
│
├── src/                             ← Web 前端
│   ├── index.html
│   ├── main.js                      ← 入口 + 路由
│   ├── styles/
│   │   ├── theme.css                ← 柚子橘暖色主题变量
│   │   └── components.css           ← 圆角卡片/按钮/进度圈
│   ├── lib/
│   │   ├── tauri-api.js             ← invoke() + listen() 封装
│   │   └── sternberg-engine.js      ← Canvas Sternberg 试次渲染引擎
│   ├── pages/
│   │   ├── home.js                  ← 4-tab 导航
│   │   ├── subjects.js              ← 被试列表 + 添加
│   │   ├── screening-start.js       ← 选被试 + 开始闯关
│   │   ├── screening-running.js     ← 进度圈 + 鼓励话术
│   │   ├── screening-result.js      ← 风险环 + 特征 bar
│   │   ├── training.js              ← Unity 游戏启动
│   │   └── reports.js               ← 历史记录
│   ├── components/
│   │   ├── calibration-canvas.js    ← 13 点校准 Canvas
│   │   ├── sternberg-canvas.js      ← Sternberg 试次 Canvas
│   │   ├── risk-ring.js             ← 风险概率圆环
│   │   └── feature-bar.js           ← 特征重要性条
│   └── assets/
│       ├── fonts/
│       │   └── NotoSansSC-subset.woff2  ← ~2-3 MB subset 字体
│       └── images/                  ← stimulus 图片 (压缩后 ~3 MB)
│           ├── neutral/
│           └── emotional/
│
├── package.json                     ← npm 依赖 (vite + 少量 JS 库)
├── vite.config.js
└── README.md
```

---

## 四、Rust 依赖 (Cargo.toml)

```toml
[dependencies]
tauri = { version = "2", features = ["shell-open"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"

# ONNX 推理 — 替代 PyTorch, 50 MB 而非 600 MB
ort = { version = "2", features = ["load-dynamic"] }

# 摄像头
nokhwa = { version = "0.10", features = ["input-native"] }
# 备选: opencv = { version = "0.92", default-features = false, features = ["videoio"] }

# 图像处理 (crop/resize 眼部/人脸区域)
image = "0.25"
imageproc = "0.25"

# 线性代数 (Ridge 回归, 特征计算)
nalgebra = "0.33"
ndarray = "0.16"

# SQLite
rusqlite = { version = "0.32", features = ["bundled"] }

# 异步
tokio = { version = "1", features = ["full"] }

# UUID
uuid = { version = "1", features = ["v4"] }

# 日期时间
chrono = "0.4"
```

**体积估算**:
- `ort` (ONNX Runtime): ~50 MB (动态链接) 或 ~20 MB (静态, stripped)
- `nokhwa`: <1 MB
- `rusqlite`: ~2 MB (bundled SQLite)
- ONNX 模型: ~11 MB (face_mesh 3 + affnet 7 + face_detector 1)
- RF JSON: ~0.5 MB
- 前端资源: ~6 MB (字体 3 + 图片 3)
- **总计: ~25-35 MB** (vs 当前 2 GB)

---

## 五、实施阶段

### Phase 0: 模型准备 (Python 里做, 1-2 天)

在现有 Python 环境里完成一次性的模型转换, 输出给 Rust 用:

1. **AFFNet → ONNX**
   ```python
   import torch
   model = AFFNet()
   model.load_state_dict(...)
   dummy = (torch.zeros(1,3,112,112), torch.zeros(1,3,112,112),
            torch.zeros(1,3,224,224), torch.zeros(1,12))
   torch.onnx.export(model, dummy, "affnet.onnx",
                      input_names=["left","right","face","rect"],
                      output_names=["gaze"])
   ```
   然后用 Python onnxruntime 验证输出与 torch 一致 (差 < 1e-5)

2. **MediaPipe FaceMesh → ONNX**
   下载 Google 的 TFLite 模型, 用 tf2onnx 转换:
   ```bash
   pip install tf2onnx
   python -m tf2onnx.convert --tflite face_landmark.tflite --output face_mesh.onnx
   ```
   同样验证输出一致

3. **RF → JSON**
   ```python
   import json, joblib
   rf = joblib.load('random_forest.joblib')
   scaler = joblib.load('scaler.joblib')
   config = joblib.load('feature_config.joblib')
   # 导出每棵树的结构 (children, thresholds, features, values)
   trees = export_rf_to_json(rf)  # 自定义函数
   json.dump({
       'trees': trees,
       'scaler_mean': scaler.mean_.tolist(),
       'scaler_std': scaler.scale_.tolist(),
       'selected_features': config['selected_features'],
       'selected_indices': config['selected_indices'],
       'all_features': config['all_features'],
   }, open('rf_model.json', 'w'))
   ```

4. **验证**: 用 Python 加载 .onnx + .json, 跑一组 features.csv 数据,
   确认预测结果与 joblib 版完全一致

### Phase 1: Tauri 骨架 + 摄像头 (3-5 天)

1. `cargo create-tauri-app tauri-adhd`
2. 配置 Tauri 窗口 (1280x800, 标题 "ADHD 早筛与训练")
3. 前端用 Vite + vanilla JS (或 Svelte) 搭骨架
4. Rust 端: `camera.rs` 打开摄像头, 每帧转 RGB, 通过 Tauri event 推 JPEG 到前端
5. 前端: `<canvas>` 显示摄像头预览画面
6. **完工标准**: 编译出 .exe, 双击打开能看到摄像头画面

### Phase 2: 人脸检测 + 虹膜特征 (5-7 天)

1. `face_mesh.rs`: 加载 face_mesh.onnx, 推理得到 478 landmarks
2. 实现 `compute_iris_features()` (从 Python 的 6D 提取逻辑移植)
3. 实现 `estimate_pupil_size()` (虹膜地标的归一化直径)
4. 实现 `compute_ear()` (Eye Aspect Ratio 眨眼过滤)
5. 前端: 在摄像头预览上叠加 478 个地标点的可视化
6. **完工标准**: 在摄像头预览上能看到地标跟踪, 虹膜特征 6D 值实时刷新

### Phase 3: AFFNet + 注视估计 (3-5 天)

1. `gaze.rs`: 加载 affnet.onnx, 实现裁剪(眼/脸/rect) + 推理
2. 实现 `RidgeRegressor` (nalgebra 做矩阵运算, fit/predict)
3. 实现 `FixationSmoother` (自适应 α)
4. `pipeline.rs`: 组合 camera + face_mesh + gaze 成完整流水线
5. 背景线程: camera capture → face mesh → gaze → Tauri event
6. **完工标准**: 前端收到 30 Hz 的 {x, y, pupil, valid} 事件流

### Phase 4: 校准 UI (3-5 天)

1. 前端 `calibration-canvas.js`: 13 个圆点, 暖色主题, 点击采集
2. Rust `commands.rs`: `start_calibration`, `submit_calibration_click(point_idx)`
3. 每点采 30 帧, 中位数 → Ridge.fit
4. 5 点验证: 前端显示验证点, Rust 计算误差
5. **完工标准**: 13 点校准 + 5 点验证跑通, Ridge 预测屏幕坐标

### Phase 5: Sternberg 任务 (5-7 天)

1. 前端 `sternberg-canvas.js`: 用 Canvas 渲染所有试次阶段
   - Fixation (十字 500ms)
   - Encoding (圆点 750ms × 3, 间隔 500ms)
   - Maintenance fixation (500ms)
   - Distractor (blank/image/shape 500ms)
   - Probe (1500ms, 检测 F/J)
   - Feedback (500ms)
2. `sternberg.rs`: trial 生成 + distractor 序列 + 数据收集
3. 时序: 前端 `requestAnimationFrame` + `performance.now()` 控制
4. 每帧: Rust event → 前端 TrialData 记录 gaze/pupil
5. 图片 distractor: 前端 `<img>` 预加载 + Canvas drawImage
6. **完工标准**: 160 trial 完整跑通, 行为数据 + gaze/pupil 时序完整记录

### Phase 6: 推理 + 报告 (3-5 天)

1. `inference.rs`: 从 TrialResult 提取 27 维特征
   - 行为特征 7 + 瞳孔 11 + 注视 3 + 条件 6
   - pupil_slope 单位修复 (fps/1000.0)
2. RF 预测: 遍历 JSON 树, 投票, 输出概率
3. StandardScaler: 硬编码 μ/σ (from rf_model.json)
4. `storage.rs`: 存 report 到 SQLite
5. 前端 `screening-result.js`: 风险环 + 特征 bar + 家长说明
6. **完工标准**: 跑完 Sternberg → 自动出报告 → 存入 SQLite

### Phase 7: Shell UI + 存储 (3-5 天)

1. 前端所有页面: 被试管理 / 早筛启动 / 进度 / 结果 / 历史
2. SQLite CRUD: subjects / sessions / reports
3. 柚子橘暖色主题 CSS
4. 字体 subset (fonttools) 嵌入
5. **完工标准**: 完整的用户流程, 从添加被试到查看历史报告

### Phase 8: Unity 游戏接入 (2-3 天)

1. `game.rs`: spawn Unity .exe, stdin/stdout JSON 通信
2. 前端 "训练" tab: 选被试 → invoke launch_unity_game
3. Rust 把 gaze 流通过 stdin 写给 Unity
4. Unity 的分数通过 stdout 读回来
5. **完工标准**: 从 Tauri UI 启动 Unity 游戏, 游戏接收 gaze 数据

### Phase 9: 打包 + 测试 (2-3 天)

1. `cargo tauri build` 生成 Win .msi / Mac .dmg
2. stimulus 图片全部压缩到 800×600 JPG
3. 字体 subset 到常用 3000 CJK 字
4. 端到端测试: 冷启动 → 校准 → 160 trial → 报告 → 游戏启动
5. Windows + Mac 各跑一次
6. **完工标准**: 双击 .exe → 全功能可用, 体积 < 35 MB (不含 Unity)

---

## 六、总工作量

| Phase | 天数 | 风险 |
|---|---|---|
| 0 - 模型准备 (Python) | 1-2 | 低 — 只是格式转换 |
| 1 - Tauri 骨架 + 摄像头 | 3-5 | 低 — nokhwa 有成熟例子 |
| 2 - 人脸检测 + 虹膜 | 5-7 | **中高** — MediaPipe ONNX 预/后处理复杂 |
| 3 - AFFNet + 注视 | 3-5 | 中 — ONNX 转换可能有边界情况 |
| 4 - 校准 UI | 3-5 | 低 — 纯前端交互 + 线性代数 |
| 5 - Sternberg 任务 | 5-7 | 中 — 时序精度需要仔细验证 |
| 6 - 推理 + 报告 | 3-5 | 低 — 纯数学移植 |
| 7 - Shell UI | 3-5 | 低 — 纯前端 |
| 8 - Unity 游戏接入 | 2-3 | 低 — stdin/stdout 很简单 |
| 9 - 打包 + 测试 | 2-3 | 低 |
| **总计** | **30-47 天** | |

按"质量优先、不赶工"算, 大约 **6-8 周**。

---

## 七、最大风险点

### 风险 1: MediaPipe FaceMesh ONNX 转换 (Phase 2)

MediaPipe 的 face_mesh 不是一个单一模型, 而是一个 **pipeline**:
1. BlazeFace 人脸检测
2. 面部区域裁剪 + 仿射变换
3. FaceMesh 468+10 地标预测
4. 虹膜细化 (10 个虹膜点)

每一步有自己的前/后处理 (anchor 计算, NMS, 归一化等)。
Python 版靠 `mediapipe` SDK 黑盒完成, 但迁移到 Rust 需要自己实现这些步骤。

**缓解措施**:
- 用 `mediapipe-rs` crate (社区维护, 有 face mesh 支持)
- 或者先把 MediaPipe 的 C++ library 编译成 .dll/.so, Rust 通过 FFI 调用
- 或者用 Google 最新的 `MediaPipe Tasks` API (有 C/Python 绑定, 但无 Rust)

**最坏情况 fallback**: 用 geometry-only 模式 (不要 AFFNet, 只用 iris + head pose)。
MediaPipe JS 可以在一个隐藏的 WebView 里跑, 结果通过 Tauri event 传给 Rust。
这样我们用的是 Google 官方的 JS SDK, 不用自己实现 pipeline。

### 风险 2: ONNX Runtime 跨平台编译

`ort` crate 需要链接 ONNX Runtime 的 native library (.dll / .dylib / .so)。
Tauri build 时需要正确配置每个平台的库路径。
`ort` 的 `load-dynamic` feature 可以在运行时从 binary 同目录加载, 减少编译时配置。

**缓解措施**: 先在一个平台 (Mac) 跑通, 再做 Windows cross-compile。

### 风险 3: Canvas Sternberg 时序精度

Python pygame 版用 `clock.tick(30)` 控制帧率。
Web Canvas 用 `requestAnimationFrame` 通常是 60 fps (vsync)。
我们需要确保 500ms / 750ms / 1500ms 的阶段时长误差 < ±16ms (一帧)。

**缓解措施**:
- 用 `performance.now()` (微秒级) 做 wall-clock 时间判断, 不依赖帧计数
- 阶段切换由 wall-clock 触发, 不是"跑满 N 帧就换"
- 误差会记录在 trial 数据里, 特征提取时可以补偿

---

## 八、与 Python 版的核心差异

| 组件 | Python 版 | Tauri 版 |
|---|---|---|
| 摄像头 | cv2.VideoCapture (阻塞) | nokhwa (异步, 回调) |
| MediaPipe | pip mediapipe SDK | ONNX 模型 + 自实现前/后处理 |
| AFFNet | torch.load + forward | ort 加载 .onnx + run |
| Ridge 回归 | sklearn Pipeline | nalgebra 矩阵运算 |
| RF 分类器 | joblib + sklearn | JSON 树 + Rust 遍历 |
| Sternberg 渲染 | pygame 全屏 | Canvas 全屏 |
| UI | Flutter Web (Dart) | HTML/CSS/JS |
| 存储 | SQLModel (Python) | rusqlite (Rust) |
| IPC (前后端) | WebSocket + REST | Tauri invoke + events |
| 游戏 | 占位 | Unity stdin/stdout subprocess |

---

## 九、建议的第一步

**立即可做 (Phase 0)**:
1. 在 Python 里把 AFFNet export 成 ONNX + 验证
2. 在 Python 里把 RF export 成 JSON
3. 下载 MediaPipe 的 TFLite 模型 + 转 ONNX

这些是**零风险**的准备工作, 结果无论走不走 Tauri 都有用
(ONNX 版的模型也可以用来优化现有 Python 版的性能)。

确认模型可用后, 再开 Tauri 项目 (Phase 1)。
