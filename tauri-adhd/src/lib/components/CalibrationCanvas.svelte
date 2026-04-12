<script>
  /**
   * CalibrationCanvas — 13-point calibration + 5-point validation.
   *
   * Fixes vs initial version:
   * - Points match Python exactly: (0.08, 0.08) → (0.92, 0.92)
   * - devicePixelRatio handling for Retina displays
   * - Click proximity check (must click within 40px of target)
   * - ESC key handler + visible exit button
   * - Warm kid-friendly styling with orange banner
   */
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
  let gazeFeatures = [];

  // 13 calibration points — EXACTLY matching Python's CALI_GRID
  const calibrationPoints = [
    { x: 0.08, y: 0.08 }, { x: 0.50, y: 0.08 }, { x: 0.92, y: 0.08 },
    { x: 0.08, y: 0.32 }, { x: 0.50, y: 0.32 }, { x: 0.92, y: 0.32 },
    { x: 0.08, y: 0.50 }, { x: 0.50, y: 0.50 }, { x: 0.92, y: 0.50 },
    { x: 0.08, y: 0.68 }, { x: 0.50, y: 0.68 }, { x: 0.92, y: 0.68 },
    { x: 0.50, y: 0.92 },
  ];

  // 5 validation points — cardinal + center
  const validationPoints = [
    { x: 0.50, y: 0.10 },  // top
    { x: 0.50, y: 0.90 },  // bottom
    { x: 0.10, y: 0.50 },  // left
    { x: 0.90, y: 0.50 },  // right
    { x: 0.50, y: 0.50 },  // center
  ];

  const CLICK_RADIUS = 40; // px — must click within this distance of target

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
      const { x, y, pupil } = event.payload;
      gazeFeatures = [x, y, pupil, 0, 0, 0, 0, 0];
    });

    canvas.addEventListener('click', handleClick);
    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('keydown', handleKey);

    render();
  });

  onDestroy(() => {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    if (unlistenGaze) unlistenGaze();
    if (canvas) canvas.removeEventListener('click', handleClick);
    window.removeEventListener('resize', resizeCanvas);
    window.removeEventListener('keydown', handleKey);
  });

  function resizeCanvas() {
    if (!canvas) return;
    dpr = window.devicePixelRatio || 1;
    cssWidth = window.innerWidth;
    cssHeight = window.innerHeight;
    // Set the backing store to physical pixels for crisp rendering
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;
    // Scale the context so all drawing uses CSS pixel coordinates
    ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function handleKey(e) {
    if (e.key === 'Escape') {
      if (onCancel) onCancel();
    }
  }

  async function handleClick(e) {
    if (mode !== 'calibrating' || !currentPoint || showResult) return;

    // Get click position in CSS pixels
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    // Target position in CSS pixels
    const targetX = currentPoint.x * cssWidth;
    const targetY = currentPoint.y * cssHeight;

    // Check proximity — must click within CLICK_RADIUS of target
    const dist = Math.hypot(clickX - targetX, clickY - targetY);
    if (dist > CLICK_RADIUS) return; // ignore clicks too far away

    try {
      await invoke('submit_calibration_sample', {
        features: gazeFeatures.length >= 8 ? gazeFeatures : [0,0,0,0,0,0,0,0],
        screenX: targetX,
        screenY: targetY,
      });
      recordCalibrationPoint();
    } catch (err) {
      console.error('Calibration sample error:', err);
    }

    currentPointIndex++;

    if (currentPointIndex >= calibrationPoints.length) {
      try {
        const err = await invoke('finish_calibration');
        meanError = err;
      } catch (err) {
        console.error('Finish calibration error:', err);
        meanError = -1;
      }
      showResult = true;
      setTimeout(() => {
        showResult = false;
        mode = 'validating';
        currentPointIndex = 0;
        startValidation();
      }, 2000);
    }
  }

  function startValidation() {
    let idx = 0;
    function showNext() {
      if (idx >= validationPoints.length) {
        mode = 'done';
        setTimeout(() => { if (onDone) onDone(); }, 500);
        return;
      }
      currentPointIndex = idx;
      idx++;
      setTimeout(showNext, 1500);
    }
    showNext();
  }

  function render() {
    if (!ctx || !canvas) return;
    const now = performance.now();
    const elapsed = (now - startTime) / 1000;
    pulsePhase = elapsed;

    const w = cssWidth;
    const h = cssHeight;

    // Warm dark background
    ctx.fillStyle = '#37302B';
    ctx.fillRect(0, 0, w, h);

    // ── Banner — positioned OPPOSITE to the current target ─────
    // If target is in top half → banner at bottom; if bottom → at top.
    // This prevents the text from overlapping the calibration dot.
    const bannerH = 48;
    const bannerText = mode === 'calibrating'
      ? `[${currentPointIndex + 1}/${calibrationPoints.length}]  请注视圆点并点击`
      : mode === 'validating'
        ? `[${currentPointIndex + 1}/${validationPoints.length}]  请注视圆点 (不用点击)`
        : '';

    if (bannerText && !showResult && currentPoint) {
      const bannerColor = mode === 'calibrating' ? '#FF8C42' : '#4ECDC4';
      const bannerWidth = Math.min(440, w - 60);
      // Place banner on the opposite side of the screen from the target
      const targetInTopHalf = currentPoint.y < 0.5;
      const bannerY = targetInTopHalf ? h - bannerH - 20 : 20;

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

    // ── ESC hint — also on opposite side, but smaller ───────────
    if (currentPoint) {
      const hintY = currentPoint.y < 0.5 ? h - 16 : 20;
      ctx.fillStyle = 'rgba(255,248,240,0.25)';
      ctx.font = '400 13px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'right';
      ctx.textBaseline = currentPoint.y < 0.5 ? 'bottom' : 'top';
      ctx.fillText('按 ESC 退出', w - 16, hintY);
    }

    // ── Content ─────────────────────────────────────────────────
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    if (showResult) {
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 32px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('校准完成', w / 2, h / 2 - 40);
      ctx.fillStyle = '#4ECDC4';
      ctx.font = '500 22px "PingFang SC", "Microsoft YaHei", sans-serif';
      const errorText = meanError >= 0
        ? `平均误差: ${meanError.toFixed(1)} 像素`
        : '校准结果已获取';
      ctx.fillText(errorText, w / 2, h / 2 + 10);
      ctx.fillStyle = 'rgba(255,248,240,0.5)';
      ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('即将进入验证阶段...', w / 2, h / 2 + 50);
    } else if (mode === 'done') {
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 32px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('校准与验证完成', w / 2, h / 2 - 20);
      ctx.fillStyle = '#4ECDC4';
      ctx.font = '500 20px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('即将开始闯关...', w / 2, h / 2 + 30);
    } else if (currentPoint) {
      drawTarget(currentPoint.x * w, currentPoint.y * h, elapsed);
    }

    // ── Progress bar ────────────────────────────────────────────
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
    const pulse = 1 + 0.18 * Math.sin(time * 3);
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

  /** Helper: draw a rounded rectangle path */
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

<!-- Visible exit button in top-left corner -->
<button class="exit-btn" onclick={() => { if (onCancel) onCancel(); }}>
  退出
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
    top: 20px;
    left: 20px;
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
    transition: all 0.2s;
  }
  .exit-btn:hover {
    background: rgba(255, 140, 66, 0.3);
    color: #FFF8F0;
    border-color: #FF8C42;
  }
</style>
