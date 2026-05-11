<script>
  // 13-point calibration followed by a short validation pass.
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';
  import { onMount, onDestroy } from 'svelte';
  import { calibration, recordCalibrationPoint } from '$lib/stores/session.js';

  let { onDone, onCancel } = $props();

  let canvas = $state(null);
  let ctx = $state(null);
  let animFrameId = null;
  let unlistenGaze = null;

  let mode = $state('calibrating');
  let currentPointIndex = $state(0);
  let meanError = $state(null);
  let showResult = $state(false);
  let errorBanner = $state(null);
  let errorBannerTimer = null;
  // Guards async work after ESC or route changes.
  let isDestroyed = false;
  let pendingTimers = [];
  let doneTimer = null;
  let postCalibTimer = null;

  let lastGaze = { x: null, y: null, ts: 0 };

  let collecting = $state(false);
  let collectStartTime = $state(0);
  let collectSamples = $state(0);
  let collectIntervalId = null;

  const SETTLE_MS = 300;
  const COLLECT_MS = 3000;
  const SAMPLE_INTERVAL = 200;  // attempt a sample every 200ms
  const MIN_SAMPLES = 2;        // minimum successful samples to accept the point

  let validationErrors = [];
  let validationBuffer = [];
  let validationCollecting = false;

  // Same 13-point grid used by the backend/Python calibration.
  const calibrationPoints = [
    { x: 0.08, y: 0.08 }, { x: 0.50, y: 0.08 }, { x: 0.92, y: 0.08 },
    { x: 0.08, y: 0.32 }, { x: 0.50, y: 0.32 }, { x: 0.92, y: 0.32 },
    { x: 0.08, y: 0.50 }, { x: 0.50, y: 0.50 }, { x: 0.92, y: 0.50 },
    { x: 0.08, y: 0.68 }, { x: 0.50, y: 0.68 }, { x: 0.92, y: 0.68 },
    { x: 0.50, y: 0.92 },
  ];

  const validationPoints = [
    { x: 0.50, y: 0.10 },  // top
    { x: 0.50, y: 0.90 },  // bottom
    { x: 0.10, y: 0.50 },  // left
    { x: 0.90, y: 0.50 },  // right
    { x: 0.50, y: 0.50 },  // center
  ];

  const CLICK_RADIUS = 40;

  let activePoints = $derived(mode === 'calibrating' ? calibrationPoints : validationPoints);
  let totalPoints = $derived(activePoints.length);
  let currentPoint = $derived(
    currentPointIndex < activePoints.length ? activePoints[currentPointIndex] : null
  );

  let pulsePhase = 0;
  let startTime = 0;
  let cssWidth = 0;
  let cssHeight = 0;
  let dpr = 1;

  onMount(async () => {
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    dpr = window.devicePixelRatio || 1;
    resizeCanvas();

    startTime = performance.now();

    unlistenGaze = await listen('gaze_frame', (event) => {
      const { x, y } = event.payload;
      lastGaze = { x, y, ts: performance.now() };
      if (validationCollecting && Number.isFinite(x) && Number.isFinite(y)) {
        validationBuffer.push([x, y]);
      }
    });

    canvas.addEventListener('click', handleClick);
    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('keydown', handleKey);

    render();
  });

  onDestroy(() => {
    isDestroyed = true;
    if (animFrameId) cancelAnimationFrame(animFrameId);
    if (unlistenGaze) unlistenGaze();
    if (errorBannerTimer) clearTimeout(errorBannerTimer);
    if (doneTimer) clearTimeout(doneTimer);
    if (postCalibTimer) clearTimeout(postCalibTimer);
    if (collectIntervalId) clearInterval(collectIntervalId);
    for (const t of pendingTimers) clearTimeout(t);
    pendingTimers = [];
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
      for (const t of pendingTimers) clearTimeout(t);
      pendingTimers = [];
      if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
      if (postCalibTimer) { clearTimeout(postCalibTimer); postCalibTimer = null; }
      if (collectIntervalId) { clearInterval(collectIntervalId); collectIntervalId = null; }
      if (onCancel) onCancel();
    }
  }

  async function handleClick(e) {
    if (mode !== 'calibrating' || !currentPoint || showResult || collecting) return;
    if (isDestroyed) return;

    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    const targetX = currentPoint.x * cssWidth;
    const targetY = currentPoint.y * cssHeight;

    const dist = Math.hypot(clickX - targetX, clickY - targetY);
    if (dist > CLICK_RADIUS) return;

    collecting = true;
    collectStartTime = performance.now();
    collectSamples = 0;

    collectIntervalId = setInterval(async () => {
      if (isDestroyed) { stopCollecting(); return; }
      const elapsed = performance.now() - collectStartTime;

      // Still settling — don't collect yet
      if (elapsed < SETTLE_MS) return;

      // Collection window finished — finalize this point
      if (elapsed >= SETTLE_MS + COLLECT_MS) {
        stopCollecting();
        if (collectSamples >= MIN_SAMPLES) {
          recordCalibrationPoint();
          currentPointIndex++;
          if (currentPointIndex >= calibrationPoints.length) {
            await finishAllCalibration();
          }
        } else {
          showErrorBanner(`采样不足(${collectSamples}/${MIN_SAMPLES})，请重试此点`);
        }
        return;
      }

      try {
        await invoke('submit_calibration_sample', {
          screenX: targetX,
          screenY: targetY,
        });
        collectSamples++;
      } catch (_err) {
        // No face sample for this frame; keep collecting.
      }
    }, SAMPLE_INTERVAL);
  }

  function stopCollecting() {
    if (collectIntervalId) {
      clearInterval(collectIntervalId);
      collectIntervalId = null;
    }
    collecting = false;
  }

  async function finishAllCalibration() {
    try {
      await invoke('finish_calibration');
    } catch (err) {
      if (isDestroyed) return;
      const msg = typeof err === 'string' ? err : (err?.message ?? String(err));
      console.error('Finish calibration error:', msg);
      showErrorBanner(msg);
    }
    if (isDestroyed) return;
    showResult = true;
    postCalibTimer = setTimeout(() => {
      postCalibTimer = null;
      if (isDestroyed) return;
      showResult = false;
      mode = 'validating';
      currentPointIndex = 0;
      startValidation();
    }, 1500);
  }

  function showErrorBanner(text) {
    errorBanner = text;
    if (errorBannerTimer) clearTimeout(errorBannerTimer);
    errorBannerTimer = setTimeout(() => { errorBanner = null; }, 3000);
  }

  function startValidation() {
    if (isDestroyed) return;
    validationErrors = [];
    let idx = 0;
    function showNext() {
      if (isDestroyed) return;
      if (idx >= validationPoints.length) {
        finalizeValidation();
        return;
      }
      currentPointIndex = idx;
      const pt = validationPoints[idx];
      const targetX = pt.x * cssWidth;
      const targetY = pt.y * cssHeight;

      validationBuffer = [];
      validationCollecting = false;

      // First 1500ms: let the eyes settle. Last 2000ms: collect gaze samples.
      // Needs generous windows because some cameras only deliver 1-3 fps.
      const t1 = setTimeout(() => {
        if (isDestroyed) return;
        validationBuffer = [];
        validationCollecting = true;
      }, 1500);
      pendingTimers.push(t1);
      const t2 = setTimeout(() => {
        if (isDestroyed) return;
        validationCollecting = false;
        if (validationBuffer.length < 2) {
          validationErrors.push(NaN);
        } else {
          let sx = 0, sy = 0;
          for (const [gx, gy] of validationBuffer) { sx += gx; sy += gy; }
          const avgX = sx / validationBuffer.length;
          const avgY = sy / validationBuffer.length;
          validationErrors.push(Math.hypot(avgX - targetX, avgY - targetY));
        }
        idx++;
        showNext();
      }, 3500);
      pendingTimers.push(t2);
    }
    showNext();
  }

  function finalizeValidation() {
    if (isDestroyed) return;
    const valid = validationErrors.filter((e) => Number.isFinite(e));
    if (valid.length < 1) {
      meanError = -1;
      showErrorBanner('验证阶段未能稳定采集到注视数据');
    } else {
      meanError = valid.reduce((a, b) => a + b, 0) / valid.length;
    }
    mode = 'done';
    showResult = true;

    // Failed validation stays on this screen; success advances after a short pause.
    if (meanError !== null && meanError >= 0) {
      doneTimer = setTimeout(() => {
        doneTimer = null;
        if (isDestroyed) return;
        if (onDone) onDone();
      }, 1500);
    }
  }

  // Retry through the parent route so the backend starts from a clean session.
  function retryCalibration() {
    if (isDestroyed) return;
    isDestroyed = true;
    if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
    if (postCalibTimer) { clearTimeout(postCalibTimer); postCalibTimer = null; }
    if (collectIntervalId) { clearInterval(collectIntervalId); collectIntervalId = null; }
    for (const t of pendingTimers) clearTimeout(t);
    pendingTimers = [];
    if (onCancel) onCancel();
  }

  function render() {
    if (!ctx || !canvas) return;
    const now = performance.now();
    const elapsed = (now - startTime) / 1000;
    pulsePhase = elapsed;

    const w = cssWidth;
    const h = cssHeight;

    ctx.fillStyle = '#37302B';
    ctx.fillRect(0, 0, w, h);

    const bannerH = 48;
    const bannerText = mode === 'calibrating'
      ? (collecting
          ? `[${currentPointIndex + 1}/${calibrationPoints.length}]  正在采集... (${collectSamples} 样本)`
          : `[${currentPointIndex + 1}/${calibrationPoints.length}]  请注视圆点并点击`)
      : mode === 'validating'
        ? `[${currentPointIndex + 1}/${validationPoints.length}]  请注视圆点 (不用点击)`
        : '';

    if (bannerText && !showResult && currentPoint) {
      const bannerColor = mode === 'calibrating' ? '#FF8C42' : '#4ECDC4';
      const bannerWidth = Math.min(500, w - 60);
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

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    if (mode === 'done' && showResult) {
      const failed = !(meanError != null && meanError >= 0);
      if (failed) {
        ctx.fillStyle = '#E94F4F';
        ctx.font = '800 34px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText('校准失败，请重做', w / 2, h / 2 - 50);
        ctx.fillStyle = '#FFF8F0';
        ctx.font = '500 18px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText('验证阶段未能稳定采集到注视数据', w / 2, h / 2 - 10);
        ctx.fillStyle = 'rgba(255,248,240,0.6)';
        ctx.font = '400 15px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText('请点击下方按钮重新校准，或按 ESC 退出', w / 2, h / 2 + 20);
      } else {
        ctx.fillStyle = '#FFF8F0';
        ctx.font = '700 32px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText('校准与验证完成', w / 2, h / 2 - 40);
        ctx.fillStyle = '#4ECDC4';
        ctx.font = '500 22px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText(`平均误差: ${meanError.toFixed(1)} 像素`, w / 2, h / 2 + 10);
        ctx.fillStyle = 'rgba(255,248,240,0.5)';
        ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText('即将开始闯关...', w / 2, h / 2 + 50);
      }
    } else if (showResult) {
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 32px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('校准完成', w / 2, h / 2 - 20);
      ctx.fillStyle = 'rgba(255,248,240,0.5)';
      ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('即将进入验证阶段...', w / 2, h / 2 + 30);
    } else if (currentPoint) {
      drawTarget(currentPoint.x * w, currentPoint.y * h, elapsed);
    }

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

    if (mode === 'calibrating' || mode === 'validating') {
      const progress = currentPointIndex / totalPoints;
      const barH = 6;
      const barY = h - barH;
      ctx.fillStyle = 'rgba(255,255,255,0.06)';
      ctx.fillRect(0, barY, w, barH);
      ctx.fillStyle = mode === 'calibrating' ? '#FF8C42' : '#4ECDC4';
      ctx.fillRect(0, barY, w * progress, barH);
    }

    animFrameId = requestAnimationFrame(render);
  }

  function drawTarget(x, y, time) {
    const pulse = collecting ? 1 : (1 + 0.18 * Math.sin(time * 3));
    const outerR = 30 * pulse;
    const innerR = 10;

    const grad = ctx.createRadialGradient(x, y, innerR, x, y, outerR + 16);
    grad.addColorStop(0, 'rgba(78,205,196,0.45)');
    grad.addColorStop(0.6, 'rgba(78,205,196,0.12)');
    grad.addColorStop(1, 'rgba(78,205,196,0)');
    ctx.beginPath();
    ctx.arc(x, y, outerR + 16, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(x, y, outerR, 0, Math.PI * 2);
    ctx.strokeStyle = '#4ECDC4';
    ctx.lineWidth = 3;
    ctx.stroke();

    // Progress ring — shows collection progress as a sweeping arc
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

    ctx.beginPath();
    ctx.arc(x, y, innerR, 0, Math.PI * 2);
    ctx.fillStyle = '#4ECDC4';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();

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

{#if mode === 'done' && showResult && !(meanError != null && meanError >= 0)}
  <button
    class="retry-btn"
    onclick={retryCalibration}
  >
    重新校准
  </button>
{/if}

<button
  class="exit-btn"
  onclick={() => {
    isDestroyed = true;
    for (const t of pendingTimers) clearTimeout(t);
    pendingTimers = [];
    if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
    if (postCalibTimer) { clearTimeout(postCalibTimer); postCalibTimer = null; }
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

  .retry-btn {
    position: fixed;
    z-index: 1001;
    left: 50%;
    top: calc(50% + 90px);
    transform: translateX(-50%);
    background: #FF8C42;
    color: #FFF8F0;
    border: 2px solid rgba(255, 255, 255, 0.4);
    border-radius: 28px;
    padding: 14px 36px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 8px 24px rgba(255, 140, 66, 0.4);
    transition: transform 0.15s ease, box-shadow 0.2s ease;
  }
  .retry-btn:hover {
    transform: translateX(-50%) translateY(-2px);
    box-shadow: 0 12px 28px rgba(255, 140, 66, 0.5);
  }
  .retry-btn:active {
    transform: translateX(-50%) translateY(0);
  }
</style>
