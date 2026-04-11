# model 模块技术文档
---

## 一、项目总览

`model/` 目录是一个完整的 **ADHD（注意力缺陷与多动障碍）早期筛查系统**，整合了：

1. **注视追踪深度学习模型**（AFFNet）的训练与推理
2. **Sternberg 视觉空间工作记忆任务**（实验范式）
3. **瞳孔/眼动特征提取 + 机器学习分类器**（ADHD vs Control）
4. **端到端实时筛查流程**：摄像头 → 校准 → 任务执行 → 特征提取 → 模型预测 → 生成报告

### 目录结构

```
model/
├── convert_pupil_data.m              # MATLAB 数据格式转换脚本
├── adhd_pupil_classifier/            # ADHD 分类器（离线训练 + 模型保存）
│   ├── adhd_classifier.py            # 核心训练pipeline
│   ├── features.csv                  # 提取的特征矩阵
│   ├── results.json                  # 模型评估结果
│   └── saved_models/                 # 保存的模型文件
│       ├── random_forest.joblib      # 随机森林（最佳模型, 80% LOOCV）
│       ├── xgboost.joblib            # XGBoost
│       ├── svm_rbf.joblib            # SVM-RBF
│       ├── scaler.joblib             # StandardScaler 标准化器
│       └── feature_config.joblib     # 特征选择配置
├── eye_tracker/                      # 实时眼动追踪 + Sternberg 任务
│   ├── itracker.py                   # 主程序：眼动追踪系统 + 入口
│   ├── sternberg_task.py             # Sternberg WM 任务实现
│   ├── adhd_inference.py             # 实时特征提取 + 模型推理
│   ├── model_pth/                    # 注视追踪模型权重
│   │   └── affnet.pth.tar            # AFFNet 预训练权重
│   ├── stimuli/                      # 干扰刺激图片
│   │   ├── neutral/                  # 中性图片 (11张)
│   │   └── emotional/                # 情绪图片 (11张)
│   └── task_data/                    # 运行时输出目录
│       ├── behavioral_{subject}.csv  # 行为数据
│       └── adhd_report_{subject}.json# 筛查报告
└── train_model/                      # AFFNet 模型训练工具链
    ├── GazeTrack/                    # GazeTrack 训练框架
    │   ├── config.py                 # 训练配置
    │   ├── train.py                  # 模型训练脚本
    │   ├── test.py                   # 模型测试脚本
    │   └── model/                    # 网络架构定义
    │       ├── AFFNet.py             # AFFNet 网络结构
    │       ├── LoRA.py               # LoRA 参数高效微调
    │       ├── DANNAFFNet.py         # 域自适应版 AFFNet
    │       └── ...                   # 其他模型变体
    ├── MPIIFaceGaze/                 # 测试数据集
    ├── affnet.pth.tar                # 训练后的权重副本
    └── AFFNet_*.txt / *.png          # 测试结果报告与热力图
```

---

## 二、各模块详细说明

---

### 2.1 convert_pupil_data.m — MATLAB 数据格式转换

| 项目 | 说明 |
|------|------|
| **语言** | MATLAB |
| **输入** | `Pupil_dataset.mat`（MATLAB v7.3/HDF5 格式，含 table 对象） |
| **输出** | `Pupil_dataset_converted.mat`（MATLAB v7 格式，纯数组/cell） |

#### 功能

将原始 Pupil_dataset 中的 MATLAB `table` 对象转换为 Python scipy.io 可直接加载的纯数值格式。提取字段包括：

- **被试信息**: `subjects`, `ages`, `groups`（ADHD/Control/on-ADHD）
- **任务数据**: `all_trial`, `all_load`, `all_distractor`, `all_perform`, `all_rtime`
- **瞳孔时间序列**: `all_pupil` — 每个 trial 8000 个采样点（1000Hz × 8秒）
- **注视位置**: `all_position_x`, `all_position_y`
- **WISC 认知评估**: `wisc_data`, `wisc_fields`

#### 为什么需要

Python 的 `scipy.io.loadmat()` 无法直接解析 MATLAB v7.3 格式中的 `table` 对象。此脚本将数据预先"扁平化"为 cell/matrix，使 Python 可以无缝加载。

#### 使用方法

```matlab
% 在 MATLAB 中运行（需要 Pupil_dataset.mat 在同目录下）
run('convert_pupil_data.m')
% 输出: Pupil_dataset_converted.mat
```

---

### 2.2 adhd_pupil_classifier/ — ADHD 分类器训练

#### 2.2.1 adhd_classifier.py

| 项目 | 说明 |
|------|------|
| **语言** | Python 3.12 |
| **依赖** | numpy, pandas, scipy, scikit-learn, xgboost, joblib |
| **输入** | `Pupil_dataset_converted.mat` |
| **输出** | 5 个 `.joblib` 模型文件 + `features.csv` + `results.json` |

#### 完整 Pipeline

```
数据加载 → 数据清洗 → 特征工程(27维) → 特征选择(12维) → LOOCV 训练评估 → 全量训练保存模型
```

#### 步骤详解

**Step 1: 数据加载与清洗**
- 加载转换后的 `.mat` 文件
- 剔除 `on-ADHD` 组（药物治疗中的 ADHD 患者，不参与分类）
- 按 `Subject` 去重，保留 **50 个被试**（ADHD=28, Control=22）

**Step 2: 增强特征工程（27 维特征）**

特征分为 4 大类：

| 类别 | 特征数 | 具体特征 | 心理学/临床意义 |
|------|--------|----------|----------------|
| **行为特征** | 7 | `mean_rt`, `std_rt`, `cv_rt`, `rt_skewness`, `accuracy`, `omission_rate`, `rt_diff_correct_incorrect` | RT 变异系数(cv_rt)消除均值影响更鲁棒; RT 偏度捕捉注意力飘移导致的长尾慢反应（ADHD 特征） |
| **瞳孔动力学** | 11 | `pupil_max_peak`, `pupil_mean_change`, `pupil_std_of_means`, `pupil_overall_var`, `pupil_peak_latency`, `pupil_slope`, `pupil_auc`, `pupil_late_minus_early`, `pupil_slope_var`, `pupil_load_diff`, `pupil_peak_load_diff` | 瞳孔扩张反映认知负荷; 斜率(slope) = 初始动员速度; AUC = 总认知投入; 晚期-早期差值 = 注意力持续性; Load 差值 = 对认知负荷的调制能力 |
| **注视特征** | 3 | `gaze_var_x`, `gaze_var_y`, `gaze_path_normalized` | 注视分散度, 扫视路径长度 — ADHD 患者常表现为过多的不自主扫视 |
| **条件交互特征** | 6 | `mean_rt_load1`, `mean_rt_load2`, `rt_load_diff`, `acc_load_diff`, `rt_distractor_slope`, `acc_distractor_slope` | Distractor 梯度效应: RT/准确率随干扰数量(3→6)变化的斜率越陡 = 抗干扰能力越弱（ADHD 特征） |

**瞳孔基线校准方法**:
- 每个 trial 取前 500 个采样点（500ms @1000Hz）的均值作为基线
- 校准后数据 = (原始值 - 基线) / |基线|（相对变化率）

**Step 3: 联合特征选择（ANOVA + 互信息）**

| 方法 | 捕捉的关系 |
|------|-----------|
| ANOVA F-value | 线性组间差异 |
| 互信息 (MI) | 非线性统计依赖 |

两种方法分别排名后取**平均排名最优的 12 个特征**。最终选中的 12 个特征：
`mean_rt`, `std_rt`, `cv_rt`, `accuracy`, `rt_diff_correct_incorrect`, `pupil_mean_change`, `pupil_std_of_means`, `pupil_slope`, `pupil_auc`, `gaze_var_x`, `mean_rt_load1`, `mean_rt_load2`

**Step 4: LOOCV 模型训练**

| 模型 | 超参数 | Accuracy | F1 | 备注 |
|------|--------|----------|-----|------|
| **Random Forest** | 300 trees, depth=4, balanced weights | **80.0%** | **0.815** | ✅ 最佳模型 |
| XGBoost | 200 trees, depth=3, lr=0.03, L1=0.5, L2=2.0 | 78.0% | 0.800 | 强正则化防过拟合 |
| SVM-RBF | C=1.0, gamma=scale, balanced | 66.0% | 0.667 | 小样本下不稳定 |
| Soft Voting | RF + XGB + SVM 概率取均值 | 78.0% | 0.800 | 集成效果未超过 RF |

**为什么用 LOOCV**: 50 个被试的小样本下，LOOCV 是最大化训练集利用率的验证策略，每次用 49 个训练、1 个验证，重复 50 轮。

**Step 5: WISC 临床关联分析**（事后解释性分析）
- 模型预测概率 vs Arithmetic: r=-0.39, **p=0.006** ★
- 模型预测概率 vs Digit_span: r=-0.33, **p=0.021** ★
- 模型预测概率 vs Performance_score: r=-0.31, **p=0.028** ★

> WISC 数据**未参与模型训练**，仅用于验证模型预测的临床合理性。ADHD 概率越高，算术/数字广度/操作得分越低，与临床知识一致。

**Step 6: 全量训练并保存模型**
- 用全部 50 个被试进行最终训练
- 保存模型、标准化器、特征配置到 `saved_models/`

#### 创新点

1. **瞳孔时间动力学特征**: 不仅看瞳孔大小均值，还提取峰值延迟、扩张斜率、AUC、早/晚期对比等时序特征
2. **联合特征选择**: ANOVA + 互信息的平均排名法，兼顾线性和非线性信息
3. **Distractor 梯度效应**: 利用 RT/准确率随干扰类型变化的线性斜率作为抗干扰能力的量化指标
4. **Load × 瞳孔交互**: 通过 Load1 vs Load2 的瞳孔差值衡量大脑对认知负荷变化的调制能力

#### 使用方法

```bash
cd model/adhd_pupil_classifier
python adhd_classifier.py
# 需要: ../Pupil_dataset_converted.mat
# 输出: saved_models/, features.csv, results.json
```

#### 2.2.2 saved_models/ — 模型文件说明

| 文件 | 内容 | 大小 |
|------|------|------|
| `random_forest.joblib` | 300棵决策树的随机森林模型（LOOCV 80.0%） | ~KB级 |
| `xgboost.joblib` | 200轮 XGBoost 梯度提升模型 | ~KB级 |
| `svm_rbf.joblib` | SVM-RBF 核模型（含支持向量） | ~KB级 |
| `scaler.joblib` | StandardScaler（存储了12个特征的均值和标准差） | 极小 |
| `feature_config.joblib` | 字典: `all_features`(27), `selected_features`(12), `selected_indices` | 极小 |

加载方式：
```python
import joblib
model = joblib.load('saved_models/random_forest.joblib')
scaler = joblib.load('saved_models/scaler.joblib')
config = joblib.load('saved_models/feature_config.joblib')
```

#### 2.2.3 results.json — 评估结果

包含所有模型的详细指标、混淆矩阵、Top-10 特征重要性，以及 WISC 临床关联的 Pearson 相关系数和 p 值。

#### 2.2.4 features.csv — 特征矩阵

50 行（每个被试一行） × 29 列（27 个特征 + label + subject），可用于下游分析或可视化。

---

### 2.3 eye_tracker/ — 实时眼动追踪与 Sternberg 任务

#### 2.3.1 itracker.py — 主程序（眼动追踪系统）

| 项目 | 说明 |
|------|------|
| **语言** | Python 3.12 |
| **核心依赖** | PyTorch, MediaPipe, pygame, OpenCV, scikit-learn |
| **硬件需求** | 普通网络摄像头 (30fps)、全屏显示器 |

#### 架构概览

```
            摄像头 (30fps)
                |
         MediaPipe FaceMesh
          (478 + 10 虹膜地标)
                |
        ┌───────┼───────┐
        │               │
   虹膜比率 (6D)    AFFNet 注视 (2D)
   + 头部朝向          预测
        │               │
        └───┬───────┬───┘
            │(拼接 8D)
      Ridge 回归器
      (13点校准)
            │
    屏幕坐标 (px, py)
            │
  注视感知自适应平滑器
            │
      最终注视点输出
```

#### 关键组件

**1. ModelAdapter — 深度学习注视预测**
- 自动识别模型类型（AFFNet / SimpleCNN）
- AFFNet 输入: 左眼(112×112) + 右眼(112×112) + 人脸(224×224) + 边框特征(12D)
- 输出: 2D 注视方向向量

**2. 虹膜特征提取 — compute_iris_features()**
- 从 MediaPipe 的 478 个面部地标中提取：
  - 右眼虹膜沿眼轴方向的归一化比率 (2D)
  - 左眼虹膜沿眼轴方向的归一化比率 (2D)
  - 头部偏航 (yaw) 和俯仰 (pitch) (2D)
- **6D 纯几何特征**，不依赖深度学习模型，延迟极低

**3. 瞳孔大小估算 — estimate_pupil_size()**
- 利用 MediaPipe 虹膜地标 (469-477) 的水平+垂直直径
- 用眼宽归一化消除距离影响
- 作为瞳孔扩张的代理指标 (proxy)

**4. 13 点点击式校准**
- 用户依次点击 13 个屏幕位置采集特征样本
- 每个点采集 30 帧，取中位数输入 Ridge 回归器
- Ridge(α=1.0) + StandardScaler pipeline 拟合 8D → 2D 映射

**5. 注视感知自适应平滑器 (FixationSmoother)**
- 注视态（速度 < 25px/帧）: α=0.05，极稳
- 扫视态: α 自适应增大至 0.35，快速跟随
- 避免了传统固定平滑系数的"迟钝追踪 vs 抖动"矛盾

**6. 两种运行模式**

| 模式 | 说明 | 输出 |
|------|------|------|
| `--mode sternberg` | 运行 Sternberg 任务 + ADHD 预测（默认） | 行为CSV + ADHD报告JSON |
| `--mode track` | 自由注视追踪 + ECML-HBN 格式输出 | .npy 注视序列 + 热力图 |

**ECML-HBN 兼容输出**:
- `X_px_{subject}_Video_{name}.npy` — (T, 2) 像素坐标
- `timestamps_{subject}_Video_{name}.npy` — (T,) 秒时间戳
- `metadata_{subject}_Video_{name}.json` — 元信息（分辨率/FPS/有效率等）

#### 创新点

1. **双模态注视特征融合**: 将轻量级虹膜几何特征 (6D) 与深度学习模型输出 (2D) 拼接为 8D 特征向量，兼顾速度和精度
2. **注视感知自适应平滑**: 根据实时速度自动切换注视/扫视平滑策略
3. **EAR 眨眼过滤**: 通过 Eye Aspect Ratio < 0.12 自动丢弃闭眼帧
4. **在线瞳孔代理估算**: 用 MediaPipe 虹膜地标实时估算瞳孔大小变化

#### 使用方法

```bash
cd model/eye_tracker

# 模式1: Sternberg 任务 + ADHD 筛查
python itracker.py --mode sternberg --subject S001

# 模式2: 自由注视追踪
python itracker.py --mode track --subject S001

# 可选: 指定视频刺激
python itracker.py --mode track --subject S001 --video path/to/video.mp4
```

运行流程：
1. 程序启动 → 打开摄像头 → 加载 AFFNet 模型
2. 进入 13 点校准（用户点击每个圆点）
3. 校准验证（5 个方位自动检测误差）
4. 执行选定模式的主任务

---

#### 2.3.2 sternberg_task.py — Sternberg 视觉空间工作记忆任务

| 项目 | 说明 |
|------|------|
| **范式来源** | 与 Pupil_dataset 训练数据完全一致 |
| **总试次** | 8 blocks × 20 trials = **160 trials** |
| **时长** | 约 20-25 分钟 |

#### 单个 Trial 流程

```
Fixation (500ms)
    │
Encoding Array 1 (750ms) ── Load1: 1点 / Load2: 2点
    │
Fixation (500ms)
    │
Encoding Array 2 (750ms)
    │
Fixation (500ms)
    │
Encoding Array 3 (750ms)
    │
Distractor (500ms)  ── blank / neutral_image / emotional_image / shape
    │
Probe (1500ms)  ── 按 F(出现过) / J(没出现过)
    │
Feedback (500ms)  ── 正确/错误
```

#### 干扰类型编码

| 编码 | 名称 | 实现 |
|------|------|------|
| 3 | blank | 全黑屏 |
| 4 | neutral_image | 从 `stimuli/neutral/` 加载中性场景图片 |
| 5 | emotional_image | 从 `stimuli/emotional/` 加载情绪图片 |
| 6 | shape | 随机散布圆点（任务相关干扰） |

#### 图片刺激系统

- **中性图片** (11张): 书本、桌子、湖景、山景等日常场景
- **情绪图片** (11张): 猫、狗、熊猫等积极情绪内容
- 所有图片统一缩放至 `(屏高×0.35, 屏高×0.26)` 像素
- **亮度归一化**: 调整平均亮度至 128，减少瞳孔受光照干扰
- **循环使用**: 图片列表 shuffle 后按索引循环，避免重复

#### 受控随机化算法

```python
generate_distractor_sequence(n_trials=160, n_blocks=8)
```

约束条件：
1. 每 block 内 4 种干扰类型**等量分配**（每种 5 次）
2. **不允许连续 3 次相同类型**
3. **跨 block 衔接检查**: 上一 block 末尾 2 个 + 下一 block 开头 2 个也不能违反约束
4. 最多 500 次 shuffle 重试确保满足约束（实测 20/20 通过率 100%）

#### 帧级数据采集

每一帧（30fps）同时记录：
- 注视坐标 (gaze_x, gaze_y)
- 瞳孔代理大小 (pupil_proxy)
- 存储在 TrialData 的 `gaze_x_series`, `gaze_y_series`, `pupil_series` 列表中

#### 创新点

1. **图片真实干扰刺激**: 使用真实中性/情绪图片替代简单几何图形，干扰效果更生态有效
2. **亮度归一化**: 确保瞳孔测量不受图片亮度差异干扰
3. **跨 block 约束随机化**: 精确复现实验范式，消除序列效应
4. **与眼动追踪完全集成**: 无需额外硬件，用摄像头实时采集瞳孔和注视数据

---

#### 2.3.3 adhd_inference.py — 实时推理模块

| 项目 | 说明 |
|------|------|
| **输入** | 160 个 TrialData 实例（来自 SternbergTask.run()） |
| **输出** | ADHD 预测结果（概率、风险等级、解释性特征重要性） |

#### 两步推理流程

**Step 1: extract_features(trial_results, fps=30)**

从 160 个 trial 中提取与训练集**完全相同**的 27 个特征。关键适配：

| 参数 | 训练集 (1000Hz) | 实时推理 (30fps) | 自适应方案 |
|------|-----------------|-----------------|-----------|
| 基线采样点 | 500 (500ms) | fps×0.5=15 | `baseline_frames = max(1, int(fps * 0.5))` |
| 斜率窗口 | 1000 (1000ms) | min(数据/3, fps×1.0)=30 | 自适应到可用数据长度 |
| RT 单位 | 毫秒 | 秒→×1000 | `t.reaction_time * 1000.0` |

**Step 2: predict_adhd(features, model_dir)**

1. 加载 `random_forest.joblib`, `scaler.joblib`, `feature_config.joblib`
2. 按训练时相同的特征顺序构建向量
3. 选取 12 个特征 → StandardScaler → RF 预测
4. 输出风险等级:

| ADHD 概率 | 风险等级 |
|-----------|---------|
| ≥ 70% | HIGH |
| ≥ 50% | MODERATE |
| ≥ 30% | LOW |
| < 30% | MINIMAL |

#### 输出示例

```json
{
  "subject_id": "S001",
  "prediction": "ADHD",
  "adhd_probability": 0.72,
  "risk_level": "HIGH",
  "feature_importance": {
    "cv_rt": 0.172,
    "accuracy": 0.141,
    "pupil_std_of_means": 0.138,
    ...
  },
  "feature_values": {
    "cv_rt": 0.3524,
    "accuracy": 0.6875,
    ...
  },
  "model_info": "Random Forest (LOOCV Accuracy: 80.0%)"
}
```

#### 创新点

1. **帧率自适应**: 所有时间相关参数根据实际 fps 动态计算，无需硬编码
2. **单位自动转换**: 检测并转换 RT 从秒到毫秒，确保与训练数据一致
3. **NaN/Inf 清洁**: 自动将异常值替换为 0.0，模型不会因数据缺失而崩溃
4. **可解释性**: 输出每个特征的重要性权重，帮助临床医生理解预测依据

---

### 2.4 train_model/ — AFFNet 模型训练工具链

#### 2.4.1 GazeTrack/ — 训练框架

这是一个功能完整的注视估计深度学习训练工具链，支持多种模型和训练策略。

#### 支持的模型

| 模型 | 类型 | 说明 |
|------|------|------|
| **AFFNet** | 注意力特征融合网络 | 主力模型，融合左右眼 + 人脸 + 位置信息 |
| iTracker | 基础 CNN | Apple iTracker 的复现 |
| FullFace | 全脸模型 | 直接使用全脸图片预测 |
| GazeTR | Transformer | 基于 Transformer 的注视估计 |
| ResNet50 | 预训练 CNN | ImageNet 预训练的 ResNet50 |
| DANN* | 域自适应变体 | 上述模型的 DANN(Domain Adversarial) 版本 |

#### AFFNet 网络结构

```
          左眼 (112×112)          右眼 (112×112)          人脸 (224×224)
              │                       │                       │
         EyeImageModel            EyeImageModel          FaceImageModel
         (AGN + SE-Net)           (AGN + SE-Net)          (SE-Net + FC)
              │                       │                       │
              │ channel concat (256ch) │            ┌─────────┘
              └──────────┬─────────────┘            │
                    eyesMerge                   rects_fc
                  (SE + AGN)                    (12→64D)
                         │                          │
                    eyesFC (128D)               faceFC (64D)
                         │                          │
                         └──────── concat ──────────┘
                                    │
                              gaze_predictor
                              (256→128→2D)
                                    │
                              注视方向 (x, y)
```

关键模块：
- **AGN (Adaptive Group Normalization)**: 利用人脸/位置信息作为 style factor 调制眼部特征的归一化参数，实现个体自适应
- **SE-Net (Squeeze-and-Excitation)**: 通道注意力机制，自动学习重要的特征通道
- **多模态融合**: 眼 + 脸 + 位置边框三路信息的层级融合

#### 高级训练功能

| 功能 | 说明 |
|------|------|
| **LoRA 微调** | rank=16, alpha=32, 仅微调 gaze_predictor/eyesFC/faceModel.fc/eyesMerge/AGN |
| **DANN 域自适应** | 梯度反转层(GRL) + 域分类器，实现跨数据集泛化 |
| **数据重定向 (Redirection)** | 利用仿射变换生成虚拟注视方向，增强训练数据 |
| **校准点微调** | 支持 5/9/13 点校准数据的个体化微调 |
| **可复现性** | 固定随机种子(42) + 确定性模式 |

#### 训练配置要点（config.py）

```python
# 核心参数
model = 'AFFNet'
train_path = 'MPIIFaceGaze/train'
lr = 0.0001
batch_size = 64
epochs = 60
train_dataset_size = 20000
seed = 42

# LoRA 微调
fine_tune_mode = True
lora_rank = 16
lora_alpha = 32
lora_dropout = 0.407
```

#### 测试结果

| 测试集 | 模型 | 整体 L2 误差 | 说明 |
|--------|------|-------------|------|
| MPIIFaceGaze | AFFNet (affnet.pth.tar) | **48.80 px** | 2998 样本, 小距离47.67/大距离49.19 |
| GazeCapture | AFFNet (checkpoint.pth) | 72.63 px | 56 样本（测试集较小） |

测试误差热力图显示注视预测在屏幕中央区域精度较好（绿色区域 ≈ 3-4cm 误差），边缘区域误差增大（黄/橙 ≈ 5-7cm）。

#### 创新点

1. **AGN 自适应归一化**: 不同于普通 BatchNorm/GroupNorm，AGN 利用人脸上下文动态调制归一化参数，实现跨个体的自适应标准化
2. **SE 通道注意力**: 在 Eye 和 Face 模型中广泛使用 SELayer，自动学习关键通道权重
3. **LoRA 参数高效微调**: 仅训练约 1-2% 的参数即可完成个体化适配
4. **DANN 域自适应**: 解决不同设备/场景下的分布偏移问题

---

## 三、系统运行依赖

### 3.1 Python 环境

```
Python 3.12
# 核心依赖
torch >= 2.0
mediapipe >= 0.10
pygame >= 2.6
opencv-python >= 4.8
numpy >= 1.24
scipy >= 1.11
pandas >= 2.0
scikit-learn >= 1.3
xgboost >= 2.0
joblib >= 1.3
Pillow >= 10.0
```

### 3.2 硬件需求

| 组件 | 最低要求 | 推荐配置 |
|------|---------|---------|
| 摄像头 | 720p, 30fps | 1080p, 30fps |
| 显示器 | 全屏分辨率 ≥ 1366×768 | 1920×1080 |
| GPU | 无（CPU 可运行） | NVIDIA GPU + CUDA（加速推理） |
| 内存 | 4GB | 8GB+ |

### 3.3 关键路径依赖

| 路径 | 用途 |
|------|------|
| `model/eye_tracker/model_pth/affnet.pth.tar` | AFFNet 模型权重 |
| `model/adhd_pupil_classifier/saved_models/` | ADHD 分类器模型 |
| `model/eye_tracker/stimuli/neutral/` | 中性干扰图片 |
| `model/eye_tracker/stimuli/emotional/` | 情绪干扰图片 |
| `itraker/GazeTrack/` (workspace root) | AFFNet 模型定义代码 |
| `C:\Windows\Fonts\msyh.ttc` | 中文字体 (微软雅黑) |

---

## 四、端到端使用流程

### 4.1 首次部署

```bash
# 1. 确保已安装依赖
pip install torch mediapipe pygame opencv-python numpy scipy pandas scikit-learn xgboost joblib Pillow

# 2. 转换原始数据（如果需要重新训练 ADHD 分类器）
# 在 MATLAB 中运行 convert_pupil_data.m

# 3. 训练 ADHD 分类器（首次需要，已有 saved_models/ 则跳过）
cd model/adhd_pupil_classifier
python adhd_classifier.py

# 4. 确认模型文件存在
ls model/adhd_pupil_classifier/saved_models/
# 应该看到: random_forest.joblib, scaler.joblib, feature_config.joblib, ...
```

### 4.2 执行筛查

```bash
cd model/eye_tracker
python itracker.py --mode sternberg --subject CHILD_001
```

执行步骤：
1. **校准** (约 2 分钟): 点击 13 个圆点完成注视校准
2. **验证** (约 10 秒): 系统自动验证校准精度
3. **Sternberg 任务** (约 20 分钟): 完成 160 个试次
4. **自动输出**: 行为 CSV + ADHD 筛查报告 JSON

### 4.3 查看结果

```bash
# 行为数据
cat model/eye_tracker/task_data/behavioral_CHILD_001.csv

# ADHD 筛查报告
cat model/eye_tracker/task_data/adhd_report_CHILD_001.json
```

---

## 五、关键设计决策与注意事项

### 5.1 为什么选择 Random Forest 而不是深度学习？

- 样本量仅 50 人，深度学习极度过拟合
- RF 在 ≤12 维特征下表现稳定
- 输出概率校准良好，可直接用于风险分级
- 可解释性强，可定位最重要的行为/生理指标

### 5.2 瞳孔代理指标的局限性

- 使用 MediaPipe 虹膜地标估算，精度低于专业眼动仪 (如 EyeLink 1000)
- 受光照条件影响（已通过亮度归一化部分缓解）
- 推荐在**光照受控的室内环境**中使用

### 5.3 RT 单位一致性

- 训练数据 (Pupil_dataset): RT 以**毫秒**为单位 (mean ≈ 805ms)
- 实时任务 (sternberg_task.py): RT 以**秒**为单位 (time.perf_counter())
- adhd_inference.py 中已做 `× 1000.0` 转换，**不可遗漏**

### 5.4 中文显示

- 所有 UI 文本已翻译为中文
- 字体使用 `C:\Windows\Fonts\msyh.ttc` (微软雅黑)
- 非 Windows 系统需替换为其他中文字体路径

---

## 六、文件清单与可删除项

| 文件/目录 | 必要性 | 说明 |
|-----------|--------|------|
| `eye_tracker/itracker.py` | ✅ 核心 | 主程序 |
| `eye_tracker/sternberg_task.py` | ✅ 核心 | 任务范式 |
| `eye_tracker/adhd_inference.py` | ✅ 核心 | 推理模块 |
| `eye_tracker/model_pth/affnet.pth.tar` | ✅ 核心 | 模型权重 |
| `eye_tracker/stimuli/` | ✅ 核心 | 干扰图片 |
| `adhd_pupil_classifier/saved_models/` | ✅ 核心 | ADHD 模型 |
| `adhd_pupil_classifier/adhd_classifier.py` | 📋 备用 | 训练脚本（运行时不需要） |
| `adhd_pupil_classifier/features.csv` | 📋 参考 | 特征数据（运行时不需要） |
| `adhd_pupil_classifier/results.json` | 📋 参考 | 评估报告（运行时不需要） |
| `convert_pupil_data.m` | 📋 备用 | 仅重新训练时需要 |
| `train_model/` | 📋 备用 | AFFNet 训练工具链（运行时不需要） |
| `*/__pycache__/` | ❌ 可删 | Python 字节码缓存 |

---

## 七、后续开发建议

1. **扩大训练集**: 当前 50 人样本偏小，建议扩展到 200+ 以提升模型泛化性
2. **跨平台支持**: 当前中文字体路径、窗口置顶等依赖 Windows，需为 Linux/macOS 添加适配
3. **GPU 加速调度**: AFFNet 推理是主要瓶颈 (~30ms/帧)，可考虑 TensorRT 优化
4. **校准持久化**: 将校准参数保存至文件，重启时可复用
5. **多语言支持**: 将所有硬编码的中文文本提取为语言包
6. **数据加密**: 涉及医疗数据，建议对输出报告和行为 CSV 进行加密存储

---

*文档生成时间: 2026-04-10*
