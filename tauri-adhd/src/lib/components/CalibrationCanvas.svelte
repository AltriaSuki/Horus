<script>
  /**
   * CalibrationCanvas — 13-point eye-tracking calibration + 5-point validation.
   *
   * Renders on a warm dark background with pulsing mint targets.
   * User clicks near each target; the click sends a calibration sample to Rust.
   * After 13 points, calls finish_calibration() to get mean error.
   * Then runs a 5-point validation (display only, no click required).
   */
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';
  import { onMount, onDestroy } from 'svelte';
  import { calibration, recordCalibrationPoint } from '$lib/stores/session.js';

  /** @type {() => void} */
  let { onDone } = $props();

  let canvas = $state(null);
  let ctx = $state(null);
  let animFrameId = null;
  let unlistenGaze = null;

  // State
  let mode = $state('calibrating'); // 'calibrating' | 'validating' | 'done'
  let currentPointIndex = $state(0);
  let meanError = $state(null);
  let showResult = $state(false);

  // Gaze data for current frame
  let gazeFeatures = [];

  // 13 calibration points — normalized (0..1)
  const calibrationPoints = [
    { x: 0.5, y: 0.5 },   // center
    { x: 0.1, y: 0.1 },   // top-left
    { x: 0.5, y: 0.1 },   // top-center
    { x: 0.9, y: 0.1 },   // top-right
    { x: 0.1, y: 0.5 },   // mid-left
    { x: 0.9, y: 0.5 },   // mid-right
    { x: 0.1, y: 0.9 },   // bottom-left
    { x: 0.5, y: 0.9 },   // bottom-center
    { x: 0.9, y: 0.9 },   // bottom-right
    { x: 0.3, y: 0.3 },   // inner quadrants
    { x: 0.7, y: 0.3 },
    { x: 0.3, y: 0.7 },
    { x: 0.7, y: 0.7 },
  ];

  // 5 validation points
  const validationPoints = [
    { x: 0.5, y: 0.5 },
    { x: 0.25, y: 0.25 },
    { x: 0.75, y: 0.25 },
    { x: 0.25, y: 0.75 },
    { x: 0.75, y: 0.75 },
  ];

  let activePoints = $derived(mode === 'calibrating' ? calibrationPoints : validationPoints);
  let totalPoints = $derived(activePoints.length);
  let currentPoint = $derived(currentPointIndex < activePoints.length ? activePoints[currentPointIndex] : null);

  // Animation
  let pulsePhase = 0;
  let startTime = 0;

  onMount(async () => {
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    startTime = performance.now();

    // Listen for gaze events to collect features for calibration
    unlistenGaze = await listen('gaze_frame', (event) => {
      const { x, y, pupil } = event.payload;
      // Accumulate raw gaze features for the current point
      gazeFeatures = [x, y, pupil, 0, 0, 0, 0, 0]; // 8D placeholder
    });

    // Handle clicks
    canvas.addEventListener('click', handleClick);

    // Handle resize
    window.addEventListener('resize', handleResize);

    // Start render loop
    render();
  });

  onDestroy(() => {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    if (unlistenGaze) unlistenGaze();
    if (canvas) {
      canvas.removeEventListener('click', handleClick);
    }
    window.removeEventListener('resize', handleResize);
  });

  function handleResize() {
    if (!canvas) return;
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }

  async function handleClick(e) {
    if (mode === 'done' || showResult) return;

    if (mode === 'calibrating' && currentPoint) {
      const screenX = currentPoint.x * canvas.width;
      const screenY = currentPoint.y * canvas.height;

      // Send calibration sample
      try {
        await invoke('submit_calibration_sample', {
          features: gazeFeatures.length >= 8 ? gazeFeatures : [0, 0, 0, 0, 0, 0, 0, 0],
          screenX: screenX,
          screenY: screenY,
        });
        recordCalibrationPoint();
      } catch (err) {
        console.error('Calibration sample error:', err);
      }

      // Advance to next point
      currentPointIndex++;

      if (currentPointIndex >= calibrationPoints.length) {
        // Finish calibration
        try {
          const err = await invoke('finish_calibration');
          meanError = err;
        } catch (err) {
          console.error('Finish calibration error:', err);
          meanError = -1;
        }
        // Show result briefly, then move to validation
        showResult = true;
        setTimeout(() => {
          showResult = false;
          mode = 'validating';
          currentPointIndex = 0;
          // Auto-advance validation points
          startValidation();
        }, 2000);
      }
    }
  }

  function startValidation() {
    // Show each validation point for 1.5s
    let idx = 0;
    function showNext() {
      if (idx >= validationPoints.length) {
        mode = 'done';
        // Small delay then callback
        setTimeout(() => {
          if (onDone) onDone();
        }, 500);
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

    const w = canvas.width;
    const h = canvas.height;

    // Dark warm background
    ctx.fillStyle = '#1A1410';
    ctx.fillRect(0, 0, w, h);

    // Draw info text
    ctx.fillStyle = 'rgba(255, 248, 240, 0.7)';
    ctx.font = '600 18px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';

    if (showResult) {
      // Show calibration result
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 28px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('校准完成', w / 2, h / 2 - 30);
      ctx.font = '500 20px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillStyle = '#4ECDC4';
      const errorText = meanError >= 0 ? `平均误差: ${meanError.toFixed(1)} 像素` : '校准结果已获取';
      ctx.fillText(errorText, w / 2, h / 2 + 10);
      ctx.fillStyle = 'rgba(255, 248, 240, 0.5)';
      ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('即将进入验证阶段...', w / 2, h / 2 + 50);
    } else if (mode === 'calibrating') {
      // Header
      ctx.fillText(`校准点: ${currentPointIndex + 1} / ${calibrationPoints.length}`, w / 2, 40);
      ctx.fillStyle = 'rgba(255, 248, 240, 0.4)';
      ctx.font = '400 14px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('请注视圆点并点击', w / 2, 64);

      // Draw target
      if (currentPoint) {
        drawTarget(currentPoint.x * w, currentPoint.y * h, elapsed);
      }
    } else if (mode === 'validating') {
      ctx.fillText(`验证点: ${currentPointIndex + 1} / ${validationPoints.length}`, w / 2, 40);
      ctx.fillStyle = 'rgba(255, 248, 240, 0.4)';
      ctx.font = '400 14px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('请注视圆点 (无需点击)', w / 2, 64);

      if (currentPoint) {
        drawTarget(currentPoint.x * w, currentPoint.y * h, elapsed);
      }
    } else if (mode === 'done') {
      ctx.fillStyle = '#FFF8F0';
      ctx.font = '700 28px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('校准与验证完成', w / 2, h / 2 - 10);
      ctx.fillStyle = '#4ECDC4';
      ctx.font = '500 18px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('即将开始闯关...', w / 2, h / 2 + 30);
    }

    // Progress bar at bottom
    if (mode === 'calibrating' || mode === 'validating') {
      const progress = currentPointIndex / totalPoints;
      const barY = h - 6;
      ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
      ctx.fillRect(0, barY, w, 6);
      ctx.fillStyle = '#4ECDC4';
      ctx.fillRect(0, barY, w * progress, 6);
    }

    animFrameId = requestAnimationFrame(render);
  }

  function drawTarget(x, y, time) {
    // Pulsing effect
    const pulse = 1 + 0.15 * Math.sin(time * 3);
    const outerRadius = 28 * pulse;
    const innerRadius = 8;

    // Outer glow
    const gradient = ctx.createRadialGradient(x, y, innerRadius, x, y, outerRadius + 10);
    gradient.addColorStop(0, 'rgba(78, 205, 196, 0.4)');
    gradient.addColorStop(0.7, 'rgba(78, 205, 196, 0.15)');
    gradient.addColorStop(1, 'rgba(78, 205, 196, 0)');
    ctx.beginPath();
    ctx.arc(x, y, outerRadius + 10, 0, Math.PI * 2);
    ctx.fillStyle = gradient;
    ctx.fill();

    // Outer ring
    ctx.beginPath();
    ctx.arc(x, y, outerRadius, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(78, 205, 196, 0.6)';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Inner filled circle
    ctx.beginPath();
    ctx.arc(x, y, innerRadius, 0, Math.PI * 2);
    ctx.fillStyle = '#4ECDC4';
    ctx.fill();

    // Center dot
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();
  }
</script>

<canvas
  bind:this={canvas}
  class="calibration-canvas"
></canvas>

<style>
  .calibration-canvas {
    display: block;
    width: 100vw;
    height: 100vh;
    cursor: crosshair;
  }
</style>
