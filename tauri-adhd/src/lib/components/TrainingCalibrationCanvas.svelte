<script>
  /**
   * TrainingCalibrationCanvas — 15-point calibration for the Python eye tracker.
   *
   * Visually identical to CalibrationCanvas (screening), but drives calibration
   * via the headless Python eye_tracker_server.py subprocess.
   *
   * Flow: click point → collect samples (Python) → next point → train model → done.
   */
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';
  import { onMount, onDestroy } from 'svelte';

  let { onDone, onCancel } = $props();

  let canvas = $state(null);
  let ctx = $state(null);
  let animFrameId = null;

  let mode = $state('calibrating');     // 'calibrating' | 'training' | 'done'
  let currentPointIndex = $state(0);
  let collecting = $state(false);       // true while Python is collecting samples
  let trainAccuracy = $state(null);
  let showResult = $state(false);
  let errorBanner = $state(null);
  let errorBannerTimer = null;
  let isDestroyed = false;
  let doneTimer = null;

  let unlistenPointDone = null;
  let unlistenPointProgress = null;
  let unlistenTrainDone = null;
  let unlistenTrainFailed = null;
  let unlistenStatus = null;

  // 15 calibration points — matching Python's CALI_GRID exactly
  const calibrationPoints = [
    { x: 0.50, y: 0.50, label: 'center' },
    { x: 0.42, y: 0.45, label: 'center' },
    { x: 0.58, y: 0.55, label: 'center' },
    { x: 0.35, y: 0.15, label: 'up' },
    { x: 0.50, y: 0.15, label: 'up' },
    { x: 0.65, y: 0.15, label: 'up' },
    { x: 0.35, y: 0.90, label: 'down' },
    { x: 0.50, y: 0.90, label: 'down' },
    { x: 0.65, y: 0.90, label: 'down' },
    { x: 0.10, y: 0.40, label: 'left' },
    { x: 0.10, y: 0.50, label: 'left' },
    { x: 0.10, y: 0.60, label: 'left' },
    { x: 0.90, y: 0.40, label: 'right' },
    { x: 0.90, y: 0.50, label: 'right' },
    { x: 0.90, y: 0.60, label: 'right' },
  ];

  const CLICK_RADIUS = 40;

  let totalPoints = calibrationPoints.length;
  let currentPoint = $derived(
    currentPointIndex < calibrationPoints.length ? calibrationPoints[currentPointIndex] : null
  );

  let pulsePhase = 0;
  let startTime = 0;
  let cssWidth = 0;
  let cssHeight = 0;
  let dpr = 1;
  let collectStartTime = $state(0);
  let collectSamples = $state(0);
  let collectTargetSamples = $state(20);
  let collectStage = $state('idle'); // idle | settling | collecting
  // Sample count display during collection
  let lastSampleCount = $state(0);

  // Keep training calibration timing consistent with screening calibration.
  const SETTLE_MS = 300;
  const COLLECT_MS = 3000;
  const MIN_SAMPLES = 2;

  onMount(async () => {
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    dpr = window.devicePixelRatio || 1;
    resizeCanvas();
    startTime = performance.now();

    // Listen for Python events
    unlistenPointDone = await listen('eye_tracker_point_done', (event) => {
      if (isDestroyed) return;
      const { index, samples, target } = event.payload;
      lastSampleCount = samples;
      collectSamples = samples;
      if (Number.isFinite(target) && target > 0) collectTargetSamples = target;
      collecting = false;
      collectStage = 'idle';

      if (!Number.isFinite(samples) || samples < MIN_SAMPLES) {
        showErrorBanner(`采样不足(${samples || 0}/${MIN_SAMPLES})，请重试此点`);
        return;
      }

      currentPointIndex++;

      if (currentPointIndex >= calibrationPoints.length) {
        // All points collected — train model
        mode = 'training';
        invoke('eye_tracker_train').catch((e) => {
          if (isDestroyed) return;
          showErrorBanner(`训练失败: ${e}`);
          mode = 'calibrating';
        });
      }
    });

    unlistenPointProgress = await listen('eye_tracker_point_progress', (event) => {
      if (isDestroyed) return;
      const { index, samples, target } = event.payload;
      if (index !== currentPointIndex) return;
      collectSamples = samples;
      if (Number.isFinite(target) && target > 0) collectTargetSamples = target;
    });

    unlistenTrainDone = await listen('eye_tracker_train_done', (event) => {
      if (isDestroyed) return;
      const { accuracy } = event.payload;
      trainAccuracy = accuracy;
      mode = 'done';
      showResult = true;

      // Start TCP server
      invoke('eye_tracker_start_server').catch((e) => {
        console.error('Failed to start server:', e);
      });

      doneTimer = setTimeout(() => {
        doneTimer = null;
        if (isDestroyed) return;
        if (onDone) onDone();
      }, 2000);
    });

    unlistenTrainFailed = await listen('eye_tracker_train_failed', (event) => {
      if (isDestroyed) return;
      showErrorBanner('模型训练失败，请重新校准');
      mode = 'calibrating';
      currentPointIndex = 0;
    });

    canvas.addEventListener('click', handleClick);
    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('keydown', handleKey);

    render();
  });

  onDestroy(() => {
    isDestroyed = true;
    if (animFrameId) cancelAnimationFrame(animFrameId);
    if (unlistenPointDone) unlistenPointDone();
    if (unlistenPointProgress) unlistenPointProgress();
    if (unlistenTrainDone) unlistenTrainDone();
    if (unlistenTrainFailed) unlistenTrainFailed();
    if (unlistenStatus) unlistenStatus();
    if (errorBannerTimer) clearTimeout(errorBannerTimer);
    if (doneTimer) clearTimeout(doneTimer);
    if (canvas) canvas.removeEventListener('click', handleClick);
    window.removeEventListener('resize', resizeCanvas);
    window.removeEventListener('keydown', handleKey);
  });

  function resizeCanvas() {
    if (!canvas) return;
    dpr = window.devicePixelRatio || 1;
    cssWidth = window.innerWidth;
    cssHeight = window.innerHeight;
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;
    ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function handleKey(e) {
    if (e.key === 'Escape') {
      isDestroyed = true;
      if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
      if (onCancel) onCancel();
    }
  }

  async function handleClick(e) {
    if (mode !== 'calibrating' || !currentPoint || collecting) return;
    if (isDestroyed) return;

    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    const targetX = currentPoint.x * cssWidth;
    const targetY = currentPoint.y * cssHeight;

    const dist = Math.hypot(clickX - targetX, clickY - targetY);
    if (dist > CLICK_RADIUS) return;

    // Start collecting samples for this point
    collecting = true;
    collectStage = 'settling';
    collectStartTime = performance.now();
    collectSamples = 0;
    collectTargetSamples = 20;
    lastSampleCount = 0;

    try {
      await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
      if (isDestroyed) return;

      collectStage = 'collecting';
      await invoke('eye_tracker_collect_point', {
        x: currentPoint.x,
        y: currentPoint.y,
        label: currentPoint.label,
      });
    } catch (err) {
      if (isDestroyed) return;
      const msg = typeof err === 'string' ? err : (err?.message ?? String(err));
      showErrorBanner(msg);
      collecting = false;
      collectStage = 'idle';
    }
  }

  function showErrorBanner(text) {
    errorBanner = text;
    if (errorBannerTimer) clearTimeout(errorBannerTimer);
    errorBannerTimer = setTimeout(() => { errorBanner = null; }, 3000);
  }

  function render() {
    if (!ctx || !canvas) return;
    const now = performance.now();
    const elapsed = (now - startTime) / 1000;
    pulsePhase = elapsed;

    const w = cssWidth;
    const h = cssHeight;

    // Warm dark background (identical to CalibrationCanvas)
    ctx.fillStyle = '#37302B';
    ctx.fillRect(0, 0, w, h);

    // ── Banner ─────────────────────────────────────────────────
    const bannerH = 48;
    let bannerText = '';
    if (mode === 'calibrating' && !collecting) {
      bannerText = `[${currentPointIndex + 1}/${totalPoints}]  请注视圆点并点击`;
    } else if (mode === 'calibrating' && collecting) {
      bannerText = collectStage === 'settling'
        ? `[${currentPointIndex + 1}/${totalPoints}]  稳定注视中...`
        : `[${currentPointIndex + 1}/${totalPoints}]  正在采集... (${collectSamples} 样本)`;
    } else if (mode === 'training') {
      bannerText = '正在训练模型，请稍候...';
    }

    if (bannerText && !showResult) {
      const bannerColor = collecting
        ? (collectStage === 'settling' ? '#FFD166' : '#4ECDC4')
        : '#FF8C42';
      const bannerWidth = Math.min(440, w - 60);
      const bannerY = h * 0.80 - bannerH / 2;

      ctx.fillStyle = 'rgba(0,0,0,0.25)';
      roundRect(ctx, (w - bannerWidth) / 2, bannerY + 2, bannerWidth, bannerH, 24);
      ctx.fill();
      ctx.fillStyle = bannerColor;
      roundRect(ctx, (w - bannerWidth) / 2, bannerY, bannerWidth, bannerH, 24);
      ctx.fill();

      ctx.fillStyle = '#FFFFFF';
      ctx.font = '700 18px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(bannerText, w / 2, bannerY + bannerH / 2);
    }

    // ── Content ─────────────────────────────────────────────────
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    if (mode === 'done' && showResult) {
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 32px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('校准与训练完成', w / 2, h / 2 - 40);
      ctx.fillStyle = '#4ECDC4';
      ctx.font = '500 22px "PingFang SC", "Microsoft YaHei", sans-serif';
      if (trainAccuracy !== null) {
        ctx.fillText(`模型准确率: ${trainAccuracy.toFixed(1)}%`, w / 2, h / 2 + 10);
      }
      ctx.fillStyle = 'rgba(255,248,240,0.5)';
      ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('即将开始训练...', w / 2, h / 2 + 50);
    } else if (mode === 'training') {
      // Show spinner text
      const dots = '.'.repeat(Math.floor(elapsed) % 4);
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 28px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText(`训练模型中${dots}`, w / 2, h / 2);
    } else if (currentPoint) {
      drawTarget(currentPoint.x * w, currentPoint.y * h, elapsed);
    }

    // ── Error banner (top center) ──────────────────────────────
    if (errorBanner) {
      const msg = errorBanner;
      ctx.font = '700 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      const tw = ctx.measureText(msg).width;
      const ebW = Math.min(w - 60, tw + 40);
      const ebH = 44;
      const ebX = (w - ebW) / 2;
      const ebY = 24;
      ctx.fillStyle = 'rgba(0,0,0,0.25)';
      roundRect(ctx, ebX, ebY + 2, ebW, ebH, 22);
      ctx.fill();
      ctx.fillStyle = '#E94F4F';
      roundRect(ctx, ebX, ebY, ebW, ebH, 22);
      ctx.fill();
      ctx.fillStyle = '#FFFFFF';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(msg, w / 2, ebY + ebH / 2);
    }

    // ── Progress bar ────────────────────────────────────────────
    if (mode === 'calibrating') {
      const progress = currentPointIndex / totalPoints;
      const barH = 6;
      const barY = h - barH;
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.fillRect(0, barY, w, barH);
      ctx.fillStyle = '#FF8C42';
      ctx.fillRect(0, barY, w * progress, barH);
    }

    animFrameId = requestAnimationFrame(render);
  }

  function drawTarget(x, y, time) {
    const pulse = collecting ? 1 : (1 + 0.18 * Math.sin(time * 3));
    const outerR = 30 * pulse;
    const innerR = 10;

    // Glow
    const grad = ctx.createRadialGradient(x, y, innerR, x, y, outerR + 16);
    grad.addColorStop(0, 'rgba(78,205,196,0.45)');
    grad.addColorStop(0.6, 'rgba(78,205,196,0.12)');
    grad.addColorStop(1, 'rgba(78,205,196,0)');
    ctx.beginPath();
    ctx.arc(x, y, outerR + 16, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    // Outer ring
    ctx.beginPath();
    ctx.arc(x, y, outerR, 0, Math.PI * 2);
    ctx.strokeStyle = '#4ECDC4';
    ctx.lineWidth = 3;
    ctx.stroke();

    // Progress ring — same style as screening calibration
    if (collecting) {
      const elapsed = performance.now() - collectStartTime;
      const totalDuration = SETTLE_MS + COLLECT_MS;
      const progress = Math.min(1, elapsed / totalDuration);
      const startAngle = -Math.PI / 2;
      const endAngle = startAngle + progress * Math.PI * 2;
      const progressR = outerR + 8;

      ctx.beginPath();
      ctx.arc(x, y, progressR, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,255,255,0.15)';
      ctx.lineWidth = 5;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(x, y, progressR, startAngle, endAngle);
      ctx.strokeStyle = elapsed < SETTLE_MS ? '#FFD166' : '#4ECDC4';
      ctx.lineWidth = 5;
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.lineCap = 'butt';

      if (collectSamples > 0) {
        const badgeR = 12;
        const badgeX = x + outerR + 14;
        const badgeY = y - outerR - 4;
        ctx.beginPath();
        ctx.arc(badgeX, badgeY, badgeR, 0, Math.PI * 2);
        ctx.fillStyle = '#4ECDC4';
        ctx.fill();
        ctx.fillStyle = '#FFFFFF';
        ctx.font = '700 12px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${collectSamples}`, badgeX, badgeY);
      }
    }

    // Inner disc
    ctx.beginPath();
    ctx.arc(x, y, innerR, 0, Math.PI * 2);
    ctx.fillStyle = '#4ECDC4';
    ctx.fill();

    // Center white dot
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();

    // Number label in a yellow pill below the target
    const label = `${currentPointIndex + 1}`;
    ctx.font = '700 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    const tw = ctx.measureText(label).width;
    const pillW = tw + 20;
    const pillH = 26;
    let pillY = y + outerR + 18;
    if (pillY + pillH > cssHeight - 20) pillY = y - outerR - 18 - pillH;
    ctx.fillStyle = '#FFD166';
    roundRect(ctx, x - pillW / 2, pillY, pillW, pillH, pillH / 2);
    ctx.fill();
    ctx.fillStyle = '#4A3810';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, x, pillY + pillH / 2);
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }
</script>

<canvas
  bind:this={canvas}
  class="calibration-canvas"
></canvas>

<button
  class="exit-btn"
  onclick={() => {
    isDestroyed = true;
    if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
    if (onCancel) onCancel();
  }}
>
  退出 (ESC)
</button>

<style>
  .calibration-canvas {
    display: block;
    width: 100vw;
    height: 100vh;
    position: fixed;
    top: 0;
    left: 0;
    z-index: 1000;
    cursor: crosshair;
  }

  .exit-btn {
    position: fixed;
    z-index: 1001;
    background: rgba(255, 255, 255, 0.12);
    color: rgba(255, 248, 240, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 20px;
    padding: 8px 18px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    backdrop-filter: blur(8px);
    transition: all 0.35s ease;
    bottom: 20px;
    right: 20px;
  }
  .exit-btn:hover {
    background: rgba(255, 140, 66, 0.3);
    color: #FFF8F0;
    border-color: #FF8C42;
  }
</style>
