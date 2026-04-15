<script>
  /**
   * SternbergCanvas — Full Sternberg working-memory paradigm.
   *
   * Trial structure (Wainstein 2017 / Rojas-Libano 2019):
   *   Fixation (500ms) -> Encoding 3x(750ms dots + 500ms gap) -> Maintenance (500ms)
   *   -> Distractor (500ms) -> Probe (1500ms) -> Feedback (500ms)
   *
   * 160 trials: 8 blocks x 20 trials. Break screen between blocks.
   *
   * Timing: requestAnimationFrame + performance.now() wall-clock.
   * Gaze: listens to 'gaze_frame' events, appends to trial series.
   */
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';
  import { onMount, onDestroy } from 'svelte';
  import {
    recordTrial,
    phase,
    PHASES,
  } from '$lib/stores/session.js';

  /** @type {{ onAllDone: () => void, onCancel?: () => void, totalBlocks?: number, trialsPerBlock?: number }} */
  let { onAllDone, onCancel, totalBlocks = 8, trialsPerBlock = 20 } = $props();

  const totalTrials = Math.max(1, totalBlocks * trialsPerBlock);

  // ─── ESC confirm-exit overlay (Bug C) ────────────────────────────
  // When true, the render loop paints a modal over the current phase
  // asking the user to confirm exit. Task-phase timing continues to
  // advance underneath (we deliberately don't pause the paradigm —
  // the user is either coming back in <1s or leaving anyway).
  let showExitConfirm = $state(false);
  // Hit-boxes for the two confirm buttons, recomputed on each draw
  // so Canvas click dispatch matches what the user sees after resize.
  let exitConfirmButtons = { continue: null, exit: null };

  let canvas = $state(null);
  let ctx = null;
  let animFrameId = null;
  let unlistenGaze = null;

  // ═══════════════════════════════════════════════════════════════
  // Constants
  // ═══════════════════════════════════════════════════════════════
  const NUM_ENCODING_ARRAYS = 3;     // three memory arrays per trial (Wainstein 2017)
  const ENCODING_DOT_MS = 750;
  const ENCODING_GAP_MS = 500;
  const FIXATION_MS = 500;
  const MAINTENANCE_MS = 500;
  const DISTRACTOR_MS = 500;
  const PROBE_MS = 1500;
  const FEEDBACK_MS = 1500;

  // Distractor types: 3=neutral_image, 4=emotional_image, 5=gray_shape, 6=blank
  const DISTRACTOR_TYPES = [3, 4, 5, 6];

  // Grid layout for dot positions (4x4)
  const GRID_COLS = 4;
  const GRID_ROWS = 4;

  // ═══════════════════════════════════════════════════════════════
  // State
  // ═══════════════════════════════════════════════════════════════
  let screenW = 0;
  let screenH = 0;

  // Task state
  let taskPhase = 'intro';  // intro | fixation | encoding | encoding_gap | maintenance | distractor | post_distractor_fixation | probe | feedback | break | complete
  let phaseStartTime = 0;
  let planCursor = 0;       // 0-based index into trialPlan (next trial to run)
  let trialNum = 0;         // 1-based global trial number (set by setupTrial)
  let blockNum = 1;         // 1-based block number
  let trialInBlock = 0;     // 1-based trial within block

  // Current trial data
  let encodingPositions = [];   // [{col, row}, ...] — full memory set (load*3 positions)
  let currentEncodingIndex = 0; // which array we're showing (0..2)
  let currentLoad = 1;          // dots per array for current trial
  let probePosition = null;     // {col, row}
  let probeIsTarget = false;    // true => probe was in memory set
  let distractorType = 0;       // 3-6
  let distractorImage = null;   // HTMLImage or null

  // Response
  let responseKey = null;
  let responseTime = NaN;
  let probeOnsetTime = 0;

  // Gaze series for current trial
  let pupilSeries = [];
  let gazeXSeries = [];
  let gazeYSeries = [];

  // Current gaze frame
  let currentGaze = { x: 0, y: 0, pupil: 0, valid: false };

  // Image pools
  let neutralImages = [];
  let emotionalImages = [];
  let imagesLoaded = false;

  // Shuffled trial plan
  let trialPlan = [];

  // Grid cell size (computed on mount)
  let cellW = 0;
  let cellH = 0;
  let gridOffsetX = 0;
  let gridOffsetY = 0;

  // Encouragement messages for break screen
  const breakMessages = [
    '休息一下，放松眼睛',
    '你做得非常棒',
    '深呼吸，准备下一关',
    '离胜利越来越近了',
    '保持专注，继续加油',
    '你是最棒的',
    '已经过半了，坚持住',
    '快要完成了',
  ];

  // ═══════════════════════════════════════════════════════════════
  // Image loading
  // ═══════════════════════════════════════════════════════════════
  function loadImage(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  async function preloadImages() {
    // Try to load images from static/images/
    // If none exist, we'll generate placeholder shapes
    const neutralPaths = [];
    const emotionalPaths = [];

    // Attempt to load numbered images (1..20)
    for (let i = 1; i <= 20; i++) {
      neutralPaths.push(`/images/neutral/${i}.jpg`);
      emotionalPaths.push(`/images/emotional/${i}.jpg`);
    }

    const neutralResults = await Promise.all(neutralPaths.map(loadImage));
    const emotionalResults = await Promise.all(emotionalPaths.map(loadImage));

    neutralImages = neutralResults.filter((img) => img !== null);
    emotionalImages = emotionalResults.filter((img) => img !== null);
    imagesLoaded = true;
  }

  // ═══════════════════════════════════════════════════════════════
  // Trial plan generation
  // ═══════════════════════════════════════════════════════════════
  function generateTrialPlan() {
    const plan = [];

    // Build a base pool of 16 condition combinations.
    const combos = [];
    for (const load of [1, 2]) {
      for (const dType of DISTRACTOR_TYPES) {
        for (const isTarget of [true, false]) {
          combos.push({ load, distractorType: dType, isTarget });
        }
      }
    }

    // Repeat combos to fill requested total trial count.
    for (let i = 0; i < totalTrials; i++) {
      const c = combos[i % combos.length];
      plan.push({
        load: c.load,
        distractorType: c.distractorType,
        isTarget: c.isTarget,
        trialNum: 0,
        blockNum: 0,
        trialInBlock: 0,
      });
    }

    // Fisher-Yates shuffle globally across all 160 trials
    for (let i = plan.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [plan[i], plan[j]] = [plan[j], plan[i]];
    }
    // Assign trialNum / blockNum / trialInBlock AFTER shuffle
    for (let i = 0; i < plan.length; i++) {
      plan[i].trialNum = i + 1;
      plan[i].blockNum = Math.floor(i / trialsPerBlock) + 1;
      plan[i].trialInBlock = (i % trialsPerBlock) + 1;
    }
    return plan;
  }

  // ═══════════════════════════════════════════════════════════════
  // Grid helpers
  // ═══════════════════════════════════════════════════════════════
  function computeGrid() {
    // Center a 4x4 grid in the screen, each cell ~120px
    const targetCellSize = Math.min(screenW, screenH) * 0.12;
    cellW = targetCellSize;
    cellH = targetCellSize;
    const gridW = GRID_COLS * cellW;
    const gridH = GRID_ROWS * cellH;
    gridOffsetX = (screenW - gridW) / 2;
    gridOffsetY = (screenH - gridH) / 2;
  }

  function gridCellCenter(col, row) {
    return {
      x: gridOffsetX + (col + 0.5) * cellW,
      y: gridOffsetY + (row + 0.5) * cellH,
    };
  }

  function randomGridPositions(count) {
    const positions = [];
    const used = new Set();
    while (positions.length < count) {
      const col = Math.floor(Math.random() * GRID_COLS);
      const row = Math.floor(Math.random() * GRID_ROWS);
      const key = `${col},${row}`;
      if (!used.has(key)) {
        used.add(key);
        positions.push({ col, row });
      }
    }
    return positions;
  }

  // ═══════════════════════════════════════════════════════════════
  // Trial setup
  // ═══════════════════════════════════════════════════════════════
  function setupTrial(planEntry) {
    trialNum = planEntry.trialNum;
    blockNum = planEntry.blockNum;
    trialInBlock = planEntry.trialInBlock;
    distractorType = planEntry.distractorType;

    // Generate encoding positions: load dots per array * 3 arrays = load*3 unique positions
    const load = planEntry.load;
    currentLoad = load;
    encodingPositions = randomGridPositions(load * NUM_ENCODING_ARRAYS);
    currentEncodingIndex = 0;

    // Probe: 50% target (from memory set), 50% novel
    if (planEntry.isTarget) {
      probeIsTarget = true;
      probePosition = encodingPositions[Math.floor(Math.random() * encodingPositions.length)];
    } else {
      probeIsTarget = false;
      // Random position NOT in encoding set
      const encodingKeys = new Set(encodingPositions.map((p) => `${p.col},${p.row}`));
      let pos;
      do {
        pos = { col: Math.floor(Math.random() * GRID_COLS), row: Math.floor(Math.random() * GRID_ROWS) };
      } while (encodingKeys.has(`${pos.col},${pos.row}`));
      probePosition = pos;
    }

    // Pick distractor image
    distractorImage = null;
    if (distractorType === 3 && neutralImages.length > 0) {
      distractorImage = neutralImages[trialNum % neutralImages.length];
    } else if (distractorType === 4 && emotionalImages.length > 0) {
      distractorImage = emotionalImages[trialNum % emotionalImages.length];
    }

    // Reset response
    responseKey = null;
    responseTime = NaN;
    probeOnsetTime = 0;

    // Reset gaze series
    pupilSeries = [];
    gazeXSeries = [];
    gazeYSeries = [];
  }

  // ═══════════════════════════════════════════════════════════════
  // Phase transitions
  // ═══════════════════════════════════════════════════════════════
  function enterPhase(newPhase) {
    taskPhase = newPhase;
    phaseStartTime = performance.now();
  }

  function handlePhaseLogic(now) {
    const elapsed = now - phaseStartTime;

    switch (taskPhase) {
      case 'intro':
        // Show intro for 3 seconds, then start first trial
        if (elapsed >= 3000) {
          startNextTrial();
        }
        break;

      case 'fixation':
        if (elapsed >= FIXATION_MS) {
          enterPhase('encoding');
          currentEncodingIndex = 0;
        }
        break;

      case 'encoding':
        if (elapsed >= ENCODING_DOT_MS) {
          currentEncodingIndex++;
          if (currentEncodingIndex >= NUM_ENCODING_ARRAYS) {
            enterPhase('maintenance');
          } else {
            enterPhase('encoding_gap');
          }
        }
        break;

      case 'encoding_gap':
        if (elapsed >= ENCODING_GAP_MS) {
          enterPhase('encoding');
        }
        break;

      case 'maintenance':
        if (elapsed >= MAINTENANCE_MS) {
          enterPhase('distractor');
        }
        break;

      case 'distractor':
        if (elapsed >= DISTRACTOR_MS) {
          enterPhase('post_distractor_fixation');
        }
        break;

      case 'post_distractor_fixation':
        if (elapsed >= FIXATION_MS) {
          enterPhase('probe');
          probeOnsetTime = performance.now();
        }
        break;

      case 'probe':
        if (elapsed >= PROBE_MS) {
          // Time's up, no response
          finishTrial();
        }
        break;

      case 'feedback':
        if (elapsed >= FEEDBACK_MS) {
          advanceAfterFeedback();
        }
        break;

      case 'break':
        // Break lasts until user presses space.
        break;

      case 'complete':
        // Do nothing, we're done
        break;
    }
  }

  function startNextTrial() {
    if (planCursor >= trialPlan.length) {
      enterPhase('complete');
      if (onAllDone) onAllDone();
      return;
    }
    setupTrial(trialPlan[planCursor]);
    planCursor++;
    enterPhase('fixation');
  }

  function finishTrial() {
    // Determine correctness
    const correct = determineCorrect();

    const trialResult = {
      trial_num: trialNum,
      block_num: blockNum,
      load: trialPlan[planCursor - 1]?.load || 1,
      distractor_type: distractorType,
      response: responseKey,
      reaction_time: isNaN(responseTime) ? 0.0 : responseTime / 1000, // to seconds (0 if no response)
      correct: correct,
      // Preserve NaN placeholders (invalid frames) — backend skips them via .is_nan()
      pupil_series: pupilSeries.map((v) => (typeof v === 'number' ? v : Number(v))),
      gaze_x_series: gazeXSeries.map((v) => (typeof v === 'number' ? v : Number(v))),
      gaze_y_series: gazeYSeries.map((v) => (typeof v === 'number' ? v : Number(v))),
    };

    // Send to Rust
    invoke('submit_trial_result', { trial: trialResult }).catch((e) => {
      console.error('submit_trial_result error:', e);
    });

    // Record in store
    recordTrial(trialResult);

    enterPhase('feedback');
  }

  function determineCorrect() {
    if (!responseKey) return false;
    // 'f' = target was present, 'j' = target was not present
    if (probeIsTarget && responseKey === 'f') return true;
    if (!probeIsTarget && responseKey === 'j') return true;
    return false;
  }

  function advanceAfterFeedback() {
    // Check if end of block
    if (trialInBlock >= trialsPerBlock && blockNum < totalBlocks) {
      enterPhase('break');
      return;
    }
    startNextTrial();
  }

  // ═══════════════════════════════════════════════════════════════
  // Input handling
  // ═══════════════════════════════════════════════════════════════
  function handleKeyDown(e) {
    // ESC — Bug C: gives the parent (or a panicking kid) a way out.
    if (e.key === 'Escape') {
      if (showExitConfirm) {
        // ESC again while confirm is up → dismiss (safer than double-exit).
        showExitConfirm = false;
        return;
      }
      if (taskPhase === 'intro' || taskPhase === 'complete') {
        // No trials in flight → exit immediately without a confirm dialog.
        performCancel();
        return;
      }
      showExitConfirm = true;
      return;
    }

    // While confirm dialog is up, swallow task input so the child can't
    // accidentally answer the probe underneath.
    if (showExitConfirm) return;

    if (taskPhase === 'probe' && !responseKey) {
      if (e.key === 'f' || e.key === 'F') {
        responseKey = 'f';
        responseTime = performance.now() - probeOnsetTime;
        finishTrial();
      } else if (e.key === 'j' || e.key === 'J') {
        responseKey = 'j';
        responseTime = performance.now() - probeOnsetTime;
        finishTrial();
      }
    } else if (taskPhase === 'break' && e.key === ' ') {
      startNextTrial();
    }
  }

  function performCancel() {
    // Fire cancel callback if the parent provided one; otherwise fall
    // back to onAllDone so the page at least leaves the canvas screen.
    if (onCancel) onCancel();
    else if (onAllDone) onAllDone();
  }

  function handleCanvasClick(e) {
    if (!showExitConfirm) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = (btn) =>
      btn && x >= btn.x && x <= btn.x + btn.w && y >= btn.y && y <= btn.y + btn.h;
    if (hit(exitConfirmButtons.continue)) {
      showExitConfirm = false;
    } else if (hit(exitConfirmButtons.exit)) {
      showExitConfirm = false;
      performCancel();
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // Rendering
  // ═══════════════════════════════════════════════════════════════
  function render() {
    if (!ctx || !canvas) return;
    const now = performance.now();

    // Phase logic
    handlePhaseLogic(now);

    // Clear
    const w = canvas.width;
    const h = canvas.height;

    switch (taskPhase) {
      case 'intro':
        drawIntro(now);
        break;
      case 'fixation':
      case 'encoding_gap':
      case 'maintenance':
      case 'post_distractor_fixation':
        drawFixation(now);
        break;
      case 'encoding':
        drawEncoding(now);
        break;
      case 'distractor':
        drawDistractor(now);
        break;
      case 'probe':
        drawProbe(now);
        break;
      case 'feedback':
        drawFeedback(now);
        break;
      case 'break':
        drawBreak(now);
        break;
      case 'complete':
        drawComplete(now);
        break;
    }

    // ESC confirm dialog always paints on top (Bug C).
    if (showExitConfirm) {
      drawExitConfirm();
    }

    animFrameId = requestAnimationFrame(render);
  }

  function drawExitConfirm() {
    // Warm, kid-friendly modal — orange + cream palette matching the rest
    // of the app. Not a flat-gray system dialog on purpose.
    ctx.fillStyle = 'rgba(43, 24, 16, 0.65)';
    ctx.fillRect(0, 0, screenW, screenH);

    const cardW = Math.min(440, screenW - 60);
    const cardH = 260;
    const cardX = (screenW - cardW) / 2;
    const cardY = (screenH - cardH) / 2;

    // Card shadow
    ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
    drawRoundRect(cardX, cardY + 8, cardW, cardH, 24);
    ctx.fill();
    // Card body
    ctx.fillStyle = '#FFF8F0';
    drawRoundRect(cardX, cardY, cardW, cardH, 24);
    ctx.fill();
    // Orange top accent band
    ctx.fillStyle = '#FF8C42';
    drawRoundRect(cardX, cardY, cardW, 8, 24);
    ctx.fill();

    // Title
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#2B1810';
    ctx.font = '800 24px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('确定要退出吗？', cardX + cardW / 2, cardY + 60);

    // Progress summary — uses planCursor (trials started) rather than
    // trialNum so it reflects actual progress even mid-trial.
    ctx.fillStyle = '#8B6F5C';
    ctx.font = '500 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    const completed = Math.max(0, planCursor - 1);
    ctx.fillText(
      `已完成 ${completed} / ${totalTrials} 题，退出后数据将保留`,
      cardX + cardW / 2,
      cardY + 96,
    );
    ctx.fillStyle = 'rgba(139, 111, 92, 0.8)';
    ctx.font = '400 13px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('按 ESC 可以取消', cardX + cardW / 2, cardY + 120);

    // Buttons
    const btnW = 140;
    const btnH = 48;
    const btnY = cardY + cardH - btnH - 28;
    const gap = 20;
    const totalW = btnW * 2 + gap;
    const leftX = cardX + (cardW - totalW) / 2;
    const rightX = leftX + btnW + gap;

    // Continue (primary orange)
    ctx.fillStyle = '#FF8C42';
    drawRoundRect(leftX, btnY, btnW, btnH, btnH / 2);
    ctx.fill();
    ctx.fillStyle = '#FFF8F0';
    ctx.font = '700 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textBaseline = 'middle';
    ctx.fillText('继续闯关', leftX + btnW / 2, btnY + btnH / 2);

    // Exit (ghost / danger)
    ctx.fillStyle = '#FFF8F0';
    drawRoundRect(rightX, btnY, btnW, btnH, btnH / 2);
    ctx.fill();
    ctx.strokeStyle = '#E94F4F';
    ctx.lineWidth = 2;
    drawRoundRect(rightX, btnY, btnW, btnH, btnH / 2);
    ctx.stroke();
    ctx.fillStyle = '#E94F4F';
    ctx.fillText('退出', rightX + btnW / 2, btnY + btnH / 2);

    // reset baseline so later draws aren't surprised
    ctx.textBaseline = 'alphabetic';

    // Record hit-boxes for click dispatch
    exitConfirmButtons = {
      continue: { x: leftX, y: btnY, w: btnW, h: btnH },
      exit: { x: rightX, y: btnY, w: btnW, h: btnH },
    };
  }

  // Phases during which gaze/pupil data should be sampled (one sample per
  // real camera frame, delivered via gaze_frame events at ~30 Hz).
  const GAZE_SAMPLING_PHASES = new Set([
    'fixation',
    'encoding',
    'encoding_gap',
    'maintenance',
    'distractor',
    'post_distractor_fixation',
    'probe',
  ]);

  // ── Draw helpers ───────────────────────────────────────────────

  function drawRoundRect(x, y, w, h, r) {
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

  function drawBlackBg() {
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, screenW, screenH);
  }

  function drawFaintGrid() {
    ctx.strokeStyle = 'rgba(255, 248, 240, 0.04)';
    ctx.lineWidth = 1;
    for (let c = 0; c <= GRID_COLS; c++) {
      const x = gridOffsetX + c * cellW;
      ctx.beginPath();
      ctx.moveTo(x, gridOffsetY);
      ctx.lineTo(x, gridOffsetY + GRID_ROWS * cellH);
      ctx.stroke();
    }
    for (let r = 0; r <= GRID_ROWS; r++) {
      const y = gridOffsetY + r * cellH;
      ctx.beginPath();
      ctx.moveTo(gridOffsetX, y);
      ctx.lineTo(gridOffsetX + GRID_COLS * cellW, y);
      ctx.stroke();
    }
  }

  function drawCross() {
    const cx = screenW / 2;
    const cy = screenH / 2;
    const size = 18;
    ctx.strokeStyle = '#FFF8F0';
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - size, cy);
    ctx.lineTo(cx + size, cy);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx, cy - size);
    ctx.lineTo(cx, cy + size);
    ctx.stroke();
  }

  // ── Phase renderers ────────────────────────────────────────────

  function drawIntro(_now) {
    // Warm cream background
    ctx.fillStyle = '#FFF8F0';
    ctx.fillRect(0, 0, screenW, screenH);

    ctx.textAlign = 'center';

    ctx.fillStyle = '#2B1810';
    ctx.font = '800 36px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('视觉记忆挑战', screenW / 2, screenH / 2 - 60);

    ctx.fillStyle = '#8B6F5C';
    ctx.font = '500 18px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('记住出现的小圆点，判断探测点是否出现过', screenW / 2, screenH / 2 - 10);

    ctx.fillStyle = '#4ECDC4';
    ctx.font = '700 18px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('F = 出现过      J = 没出现过', screenW / 2, screenH / 2 + 40);

    ctx.fillStyle = '#8B6F5C';
    ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('即将开始...', screenW / 2, screenH / 2 + 90);

    // Trial info
    ctx.fillStyle = '#FF8C42';
    ctx.font = '600 14px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText(`共 ${totalBlocks} 关 x ${trialsPerBlock} 题`, screenW / 2, screenH / 2 + 130);
  }

  function drawFixation(_now) {
    drawBlackBg();
    drawFaintGrid();
    drawCross();
  }

  function drawEncoding(_now) {
    drawBlackBg();
    drawFaintGrid();

    // Render all `load` dots for the current array (array index = currentEncodingIndex)
    const dotRadius = cellW * 0.15;
    const sliceStart = currentEncodingIndex * currentLoad;
    const sliceEnd = sliceStart + currentLoad;
    for (let i = sliceStart; i < sliceEnd && i < encodingPositions.length; i++) {
      const pos = encodingPositions[i];
      const { x, y } = gridCellCenter(pos.col, pos.row);

      ctx.beginPath();
      ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
      ctx.fillStyle = '#FFFFFF';
      ctx.fill();

      ctx.beginPath();
      ctx.arc(x, y, dotRadius + 2, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  function drawDistractor(_now) {
    // Gray background (40,40,40)
    ctx.fillStyle = '#282828';
    ctx.fillRect(0, 0, screenW, screenH);

    const cx = screenW / 2;
    const cy = screenH / 2;
    const imgSize = Math.min(screenW, screenH) * 0.4;

    switch (distractorType) {
      case 3: // neutral image
      case 4: // emotional image
        if (distractorImage) {
          const aspect = distractorImage.width / distractorImage.height;
          let drawW = imgSize;
          let drawH = imgSize / aspect;
          if (drawH > imgSize) {
            drawH = imgSize;
            drawW = imgSize * aspect;
          }
          ctx.drawImage(distractorImage, cx - drawW / 2, cy - drawH / 2, drawW, drawH);
        } else {
          // Fallback: colored rectangle placeholder
          ctx.fillStyle = distractorType === 3 ? '#3A3A4A' : '#4A3A3A';
          ctx.fillRect(cx - imgSize / 2, cy - imgSize / 2, imgSize, imgSize);
          ctx.fillStyle = 'rgba(255,255,255,0.2)';
          ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(distractorType === 3 ? '中性图片' : '情绪图片', cx, cy);
        }
        break;

      case 5: // gray shape
        // Draw a simple geometric shape
        ctx.fillStyle = '#505060';
        ctx.beginPath();
        // Diamond shape
        ctx.moveTo(cx, cy - imgSize * 0.3);
        ctx.lineTo(cx + imgSize * 0.3, cy);
        ctx.lineTo(cx, cy + imgSize * 0.3);
        ctx.lineTo(cx - imgSize * 0.3, cy);
        ctx.closePath();
        ctx.fill();
        break;

      case 6: // blank
        // Just the gray background, nothing more
        break;
    }
  }

  function drawProbe(_now) {
    drawBlackBg();
    drawFaintGrid();

    if (probePosition) {
      const { x, y } = gridCellCenter(probePosition.col, probePosition.row);
      const dotRadius = cellW * 0.15;

      // Yellow probe dot
      ctx.beginPath();
      ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
      ctx.fillStyle = '#FFD166';
      ctx.fill();

      // Glow
      const grad = ctx.createRadialGradient(x, y, dotRadius * 0.5, x, y, dotRadius * 1.5);
      grad.addColorStop(0, 'rgba(255, 209, 102, 0.3)');
      grad.addColorStop(1, 'rgba(255, 209, 102, 0)');
      ctx.beginPath();
      ctx.arc(x, y, dotRadius * 1.5, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // Response hint at bottom
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(255, 248, 240, 0.5)';
    ctx.font = '500 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('F = 出现过      J = 没出现过', screenW / 2, screenH - 40);
  }

  function drawFeedback(_now) {
    drawBlackBg();

    const cx = screenW / 2;
    const cy = screenH / 2;
    const correct = determineCorrect();

    if (correct) {
      // Mint circle with checkmark
      ctx.beginPath();
      ctx.arc(cx, cy - 20, 40, 0, Math.PI * 2);
      ctx.fillStyle = '#4ECDC4';
      ctx.fill();

      // Checkmark
      ctx.strokeStyle = '#FFFFFF';
      ctx.lineWidth = 4;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(cx - 14, cy - 20);
      ctx.lineTo(cx - 4, cy - 10);
      ctx.lineTo(cx + 16, cy - 32);
      ctx.stroke();

      // Text
      ctx.textAlign = 'center';
      ctx.fillStyle = '#4ECDC4';
      ctx.font = '800 24px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('太棒了', cx, cy + 50);
      ctx.fillStyle = 'rgba(78, 205, 196, 0.6)';
      ctx.font = '500 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('继续加油', cx, cy + 80);
    } else {
      // Orange circle with arrow
      ctx.beginPath();
      ctx.arc(cx, cy - 20, 40, 0, Math.PI * 2);
      ctx.fillStyle = '#FF8C42';
      ctx.fill();

      // Forward arrow
      ctx.strokeStyle = '#FFFFFF';
      ctx.lineWidth = 4;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(cx - 12, cy - 20);
      ctx.lineTo(cx + 12, cy - 20);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx + 4, cy - 30);
      ctx.lineTo(cx + 14, cy - 20);
      ctx.lineTo(cx + 4, cy - 10);
      ctx.stroke();

      // Text
      ctx.textAlign = 'center';
      ctx.fillStyle = '#FF8C42';
      ctx.font = '800 24px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('没关系', cx, cy + 50);
      ctx.fillStyle = 'rgba(255, 140, 66, 0.6)';
      ctx.font = '500 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('下一题加油', cx, cy + 80);
    }
  }

  function drawBreak(now) {
    // Warm cream background
    ctx.fillStyle = '#FFF8F0';
    ctx.fillRect(0, 0, screenW, screenH);

    const cx = screenW / 2;
    const cy = screenH / 2;
    const msgIndex = Math.min(blockNum - 1, breakMessages.length - 1);

    ctx.textAlign = 'center';

    // Block completion
    ctx.fillStyle = '#FF8C42';
    ctx.font = '800 28px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText(`第 ${blockNum} 关完成`, cx, cy - 60);

    // Encouragement
    ctx.fillStyle = '#2B1810';
    ctx.font = '600 22px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText(breakMessages[msgIndex], cx, cy);

    // Progress bar
    const barW = screenW * 0.5;
    const barH = 12;
    const barX = cx - barW / 2;
    const barY = cy + 40;
    const progress = blockNum / totalBlocks;

    ctx.fillStyle = '#E8DDD4';
    drawRoundRect(barX, barY, barW, barH, 6);
    ctx.fill();

    ctx.fillStyle = '#4ECDC4';
    drawRoundRect(barX, barY, Math.max(barW * progress, 1), barH, 6);
    ctx.fill();

    ctx.fillStyle = '#8B6F5C';
    ctx.font = '500 14px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText(`${blockNum} / ${totalBlocks}`, cx, barY + 36);

    // Continue hint
    const pulse = 0.5 + 0.5 * Math.sin(((now - phaseStartTime) / 1000) * 2);
    ctx.fillStyle = `rgba(139, 111, 92, ${0.4 + pulse * 0.4})`;
    ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('按 空格键 继续', cx, cy + 100);
  }

  function drawComplete(_now) {
    ctx.fillStyle = '#FFF8F0';
    ctx.fillRect(0, 0, screenW, screenH);

    const cx = screenW / 2;
    const cy = screenH / 2;

    // Big success circle
    ctx.beginPath();
    ctx.arc(cx, cy - 40, 50, 0, Math.PI * 2);
    ctx.fillStyle = '#4ECDC4';
    ctx.fill();

    // Checkmark
    ctx.strokeStyle = '#FFFFFF';
    ctx.lineWidth = 5;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - 18, cy - 40);
    ctx.lineTo(cx - 6, cy - 28);
    ctx.lineTo(cx + 20, cy - 56);
    ctx.stroke();

    ctx.textAlign = 'center';
    ctx.fillStyle = '#2B1810';
    ctx.font = '800 32px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('所有关卡完成', cx, cy + 40);

    ctx.fillStyle = '#8B6F5C';
    ctx.font = '500 18px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('正在生成报告...', cx, cy + 80);
  }

  // ═══════════════════════════════════════════════════════════════
  // Lifecycle
  // ═══════════════════════════════════════════════════════════════
  onMount(async () => {
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    screenW = window.innerWidth;
    screenH = window.innerHeight;
    canvas.width = screenW;
    canvas.height = screenH;

    computeGrid();

    // Generate trial plan
    trialPlan = generateTrialPlan();
    planCursor = 0;

    // Preload images (non-blocking)
    preloadImages();

    // Listen for gaze events. Sampling happens HERE (driven by real camera
    // frames at ~30 Hz) rather than in the RAF render loop, so the pupil /
    // gaze time series is not resampled to display refresh rate or throttled
    // when the tab loses focus.
    unlistenGaze = await listen('gaze_frame', (event) => {
      const { x, y, pupil, valid } = event.payload;
      currentGaze = { x, y, pupil, valid };

      // Only sample while a trial is active and in a sampling-whitelisted phase.
      if (trialNum === 0 || !GAZE_SAMPLING_PHASES.has(taskPhase)) return;

      // Invalid frames (or NaN coords) still push NaN placeholders so the
      // time axis stays aligned; downstream feature extraction skips NaN.
      if (valid && Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(pupil)) {
        pupilSeries.push(pupil);
        gazeXSeries.push(x);
        gazeYSeries.push(y);
      } else {
        pupilSeries.push(NaN);
        gazeXSeries.push(NaN);
        gazeYSeries.push(NaN);
      }
    });

    // Key handler
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', handleResize);
    canvas.addEventListener('click', handleCanvasClick);

    // Start intro
    enterPhase('intro');
    animFrameId = requestAnimationFrame(render);
  });

  onDestroy(() => {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    if (unlistenGaze) unlistenGaze();
    window.removeEventListener('keydown', handleKeyDown);
    window.removeEventListener('resize', handleResize);
    if (canvas) canvas.removeEventListener('click', handleCanvasClick);
  });

  function handleResize() {
    if (!canvas) return;
    screenW = window.innerWidth;
    screenH = window.innerHeight;
    canvas.width = screenW;
    canvas.height = screenH;
    computeGrid();
  }
</script>

<canvas
  bind:this={canvas}
  class="sternberg-canvas"
  class:show-cursor={showExitConfirm}
></canvas>

<style>
  .sternberg-canvas {
    display: block;
    width: 100vw;
    height: 100vh;
    cursor: none;
  }
  .sternberg-canvas.show-cursor {
    cursor: pointer;
  }
</style>
