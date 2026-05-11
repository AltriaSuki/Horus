<script>
  // Sternberg task canvas; gaze samples come from Rust `gaze_frame` events.
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

  let showExitConfirm = $state(false);
  // Recomputed on draw so clicks match the resized canvas.
  let exitConfirmButtons = { continue: null, exit: null };

  let canvas = $state(null);
  let ctx = null;
  let animFrameId = null;
  let unlistenGaze = null;

  const NUM_ENCODING_ARRAYS = 3;
  const ENCODING_DOT_MS = 750;
  const ENCODING_GAP_MS = 500;
  const FIXATION_MS = 500;
  const MAINTENANCE_MS = 500;
  const DISTRACTOR_MS = 500;
  const PROBE_MS = 1500;
  const FEEDBACK_MS = 1500;

  const DISTRACTOR_TYPES = [3, 4, 5, 6];

  const GRID_COLS = 4;
  const GRID_ROWS = 4;

  let screenW = 0;
  let screenH = 0;

  let taskPhase = 'intro';  // intro | fixation | encoding | encoding_gap | maintenance | distractor | post_distractor_fixation | probe | feedback | break | complete
  let phaseStartTime = 0;
  let planCursor = 0;       // 0-based index into trialPlan (next trial to run)
  let trialNum = 0;         // 1-based global trial number (set by setupTrial)
  let blockNum = 1;         // 1-based block number
  let trialInBlock = 0;     // 1-based trial within block

  let encodingPositions = [];
  let currentEncodingIndex = 0;
  let currentLoad = 1;
  let probePosition = null;
  let probeIsTarget = false;
  let distractorType = 0;
  let distractorImage = null;

  let responseKey = null;
  let responseTime = NaN;
  let probeOnsetTime = 0;

  let pupilSeries = [];
  let gazeXSeries = [];
  let gazeYSeries = [];

  let currentGaze = { x: 0, y: 0, pupil: 0, valid: false };

  let neutralImages = [];
  let emotionalImages = [];
  let imagesLoaded = false;

  let trialPlan = [];

  let cellW = 0;
  let cellH = 0;
  let gridOffsetX = 0;
  let gridOffsetY = 0;

  const breakMessages = [
    '休息一下，放松眼睛',
    '喝口水也可以',
    '准备好再继续',
    '下一关会马上开始',
    '眼睛看屏幕中间',
    '按空格进入下一关',
    '已经过半了',
    '快要完成了',
  ];

  function loadImage(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  async function preloadImages() {
    const neutralPaths = [];
    const emotionalPaths = [];

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

  function generateTrialPlan() {
    const plan = [];

    const combos = [];
    for (const load of [1, 2]) {
      for (const dType of DISTRACTOR_TYPES) {
        for (const isTarget of [true, false]) {
          combos.push({ load, distractorType: dType, isTarget });
        }
      }
    }

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

    for (let i = plan.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [plan[i], plan[j]] = [plan[j], plan[i]];
    }
    for (let i = 0; i < plan.length; i++) {
      plan[i].trialNum = i + 1;
      plan[i].blockNum = Math.floor(i / trialsPerBlock) + 1;
      plan[i].trialInBlock = (i % trialsPerBlock) + 1;
    }
    return plan;
  }

  function computeGrid() {
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

  function setupTrial(planEntry) {
    trialNum = planEntry.trialNum;
    blockNum = planEntry.blockNum;
    trialInBlock = planEntry.trialInBlock;
    distractorType = planEntry.distractorType;

    const load = planEntry.load;
    currentLoad = load;
    encodingPositions = randomGridPositions(load * NUM_ENCODING_ARRAYS);
    currentEncodingIndex = 0;

    if (planEntry.isTarget) {
      probeIsTarget = true;
      probePosition = encodingPositions[Math.floor(Math.random() * encodingPositions.length)];
    } else {
      probeIsTarget = false;
      const encodingKeys = new Set(encodingPositions.map((p) => `${p.col},${p.row}`));
      let pos;
      do {
        pos = { col: Math.floor(Math.random() * GRID_COLS), row: Math.floor(Math.random() * GRID_ROWS) };
      } while (encodingKeys.has(`${pos.col},${pos.row}`));
      probePosition = pos;
    }

    distractorImage = null;
    if (distractorType === 3 && neutralImages.length > 0) {
      distractorImage = neutralImages[trialNum % neutralImages.length];
    } else if (distractorType === 4 && emotionalImages.length > 0) {
      distractorImage = emotionalImages[trialNum % emotionalImages.length];
    }

    responseKey = null;
    responseTime = NaN;
    probeOnsetTime = 0;

    pupilSeries = [];
    gazeXSeries = [];
    gazeYSeries = [];
  }

  function enterPhase(newPhase) {
    taskPhase = newPhase;
    phaseStartTime = performance.now();
  }

  function handlePhaseLogic(now) {
    const elapsed = now - phaseStartTime;

    switch (taskPhase) {
      case 'intro':
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
          finishTrial();
        }
        break;

      case 'feedback':
        if (elapsed >= FEEDBACK_MS) {
          advanceAfterFeedback();
        }
        break;

      case 'break':
        break;

      case 'complete':
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
    const correct = determineCorrect();

    const sanitizeSeries = (series) => series.map((v) => (Number.isFinite(v) ? v : null));

    const trialResult = {
      trial_num: trialNum,
      block_num: blockNum,
      load: trialPlan[planCursor - 1]?.load || 1,
      distractor_type: distractorType,
      response: responseKey,
      reaction_time: isNaN(responseTime) ? 0.0 : responseTime / 1000, // to seconds (0 if no response)
      correct: correct,
      // Keep placeholders as null so Rust can treat them as invalid samples.
      pupil_series: sanitizeSeries(pupilSeries),
      gaze_x_series: sanitizeSeries(gazeXSeries),
      gaze_y_series: sanitizeSeries(gazeYSeries),
    };

    invoke('submit_trial_result', { trial: trialResult }).catch((e) => {
      console.error('submit_trial_result error:', e);
    });

    recordTrial(trialResult);

    enterPhase('feedback');
  }

  function determineCorrect() {
    if (!responseKey) return false;
    // '1' = target was present, '2' = target was not present
    if (probeIsTarget && responseKey === '1') return true;
    if (!probeIsTarget && responseKey === '2') return true;
    return false;
  }

  function advanceAfterFeedback() {
    if (trialInBlock >= trialsPerBlock && blockNum < totalBlocks) {
      enterPhase('break');
      return;
    }
    startNextTrial();
  }

  function handleKeyDown(e) {
    // ESC opens a confirmation overlay once a run has started.
    if (e.key === 'Escape') {
      if (showExitConfirm) {
        // ESC again dismisses the overlay.
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

    if (taskPhase === 'intro') {
      startNextTrial();
      return;
    }

    if (taskPhase === 'probe' && !responseKey) {
      if (e.key === '1' || e.code === 'Numpad1') {
        responseKey = '1';
        responseTime = performance.now() - probeOnsetTime;
        finishTrial();
      } else if (e.key === '2' || e.code === 'Numpad2') {
        responseKey = '2';
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

  function render() {
    if (!ctx || !canvas) return;
    const now = performance.now();

    handlePhaseLogic(now);

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

    if (showExitConfirm) {
      drawExitConfirm();
    }

    animFrameId = requestAnimationFrame(render);
  }

  function drawExitConfirm() {
    // Keep the modal in the same visual language as the task screen.
    ctx.fillStyle = 'rgba(43, 24, 16, 0.65)';
    ctx.fillRect(0, 0, screenW, screenH);

    const cardW = Math.min(440, screenW - 60);
    const cardH = 260;
    const cardX = (screenW - cardW) / 2;
    const cardY = (screenH - cardH) / 2;

    ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
    drawRoundRect(cardX, cardY + 8, cardW, cardH, 24);
    ctx.fill();
    ctx.fillStyle = '#FFF8F0';
    drawRoundRect(cardX, cardY, cardW, cardH, 24);
    ctx.fill();
    ctx.fillStyle = '#FF8C42';
    drawRoundRect(cardX, cardY, cardW, 8, 24);
    ctx.fill();

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

    const btnW = 140;
    const btnH = 48;
    const btnY = cardY + cardH - btnH - 28;
    const gap = 20;
    const totalW = btnW * 2 + gap;
    const leftX = cardX + (cardW - totalW) / 2;
    const rightX = leftX + btnW + gap;

    ctx.fillStyle = '#FF8C42';
    drawRoundRect(leftX, btnY, btnW, btnH, btnH / 2);
    ctx.fill();
    ctx.fillStyle = '#FFF8F0';
    ctx.font = '700 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textBaseline = 'middle';
    ctx.fillText('继续闯关', leftX + btnW / 2, btnY + btnH / 2);

    ctx.fillStyle = '#FFF8F0';
    drawRoundRect(rightX, btnY, btnW, btnH, btnH / 2);
    ctx.fill();
    ctx.strokeStyle = '#E94F4F';
    ctx.lineWidth = 2;
    drawRoundRect(rightX, btnY, btnW, btnH, btnH / 2);
    ctx.stroke();
    ctx.fillStyle = '#E94F4F';
    ctx.fillText('退出', rightX + btnW / 2, btnY + btnH / 2);

    ctx.textBaseline = 'alphabetic';

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

  function drawIntro(_now) {
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
    ctx.fillText('1 = 出现过      2 = 没出现过（主键盘或小键盘）', screenW / 2, screenH / 2 + 40);

    ctx.fillStyle = '#8B6F5C';
    ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('按任意键开始', screenW / 2, screenH / 2 + 90);

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
          ctx.fillStyle = distractorType === 3 ? '#3A3A4A' : '#4A3A3A';
          ctx.fillRect(cx - imgSize / 2, cy - imgSize / 2, imgSize, imgSize);
          ctx.fillStyle = 'rgba(255,255,255,0.2)';
          ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(distractorType === 3 ? '中性图片' : '情绪图片', cx, cy);
        }
        break;

      case 5: // gray shape
        ctx.fillStyle = '#505060';
        ctx.beginPath();
        ctx.moveTo(cx, cy - imgSize * 0.3);
        ctx.lineTo(cx + imgSize * 0.3, cy);
        ctx.lineTo(cx, cy + imgSize * 0.3);
        ctx.lineTo(cx - imgSize * 0.3, cy);
        ctx.closePath();
        ctx.fill();
        break;

      case 6: // blank
        break;
    }
  }

  function drawProbe(_now) {
    drawBlackBg();
    drawFaintGrid();

    if (probePosition) {
      const { x, y } = gridCellCenter(probePosition.col, probePosition.row);
      const dotRadius = cellW * 0.15;

      ctx.beginPath();
      ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
      ctx.fillStyle = '#FFD166';
      ctx.fill();

      const grad = ctx.createRadialGradient(x, y, dotRadius * 0.5, x, y, dotRadius * 1.5);
      grad.addColorStop(0, 'rgba(255, 209, 102, 0.3)');
      grad.addColorStop(1, 'rgba(255, 209, 102, 0)');
      ctx.beginPath();
      ctx.arc(x, y, dotRadius * 1.5, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(255, 248, 240, 0.5)';
    ctx.font = '500 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('1 = 出现过      2 = 没出现过（主键盘或小键盘）', screenW / 2, screenH - 40);
  }

  function drawFeedback(_now) {
    drawBlackBg();

    const cx = screenW / 2;
    const cy = screenH / 2;
    const correct = determineCorrect();

    if (correct) {
      ctx.beginPath();
      ctx.arc(cx, cy - 20, 40, 0, Math.PI * 2);
      ctx.fillStyle = '#4ECDC4';
      ctx.fill();

      ctx.strokeStyle = '#FFFFFF';
      ctx.lineWidth = 4;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(cx - 14, cy - 20);
      ctx.lineTo(cx - 4, cy - 10);
      ctx.lineTo(cx + 16, cy - 32);
      ctx.stroke();

      ctx.textAlign = 'center';
      ctx.fillStyle = '#4ECDC4';
      ctx.font = '800 24px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('太棒了', cx, cy + 50);
      ctx.fillStyle = 'rgba(78, 205, 196, 0.6)';
      ctx.font = '500 16px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.fillText('看下一题', cx, cy + 80);
    } else {
      ctx.beginPath();
      ctx.arc(cx, cy - 20, 40, 0, Math.PI * 2);
      ctx.fillStyle = '#FF8C42';
      ctx.fill();

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
    ctx.fillStyle = '#FFF8F0';
    ctx.fillRect(0, 0, screenW, screenH);

    const cx = screenW / 2;
    const cy = screenH / 2;
    const msgIndex = Math.min(blockNum - 1, breakMessages.length - 1);

    ctx.textAlign = 'center';

    ctx.fillStyle = '#FF8C42';
    ctx.font = '800 28px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText(`第 ${blockNum} 关完成`, cx, cy - 60);

    ctx.fillStyle = '#2B1810';
    ctx.font = '600 22px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText(breakMessages[msgIndex], cx, cy);

    ctx.fillStyle = '#8B6F5C';
    ctx.font = '400 16px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillText('建议休息 1 分钟', cx, cy + 34);

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

  onMount(async () => {
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    screenW = window.innerWidth;
    screenH = window.innerHeight;
    canvas.width = screenW;
    canvas.height = screenH;

    computeGrid();

    trialPlan = generateTrialPlan();
    planCursor = 0;

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

      // Invalid frames push null placeholders to keep alignment without NaN in IPC payloads.
      if (valid && Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(pupil)) {
        pupilSeries.push(pupil);
        gazeXSeries.push(x);
        gazeYSeries.push(y);
      } else {
        pupilSeries.push(null);
        gazeXSeries.push(null);
        gazeYSeries.push(null);
      }
    });

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', handleResize);
    canvas.addEventListener('click', handleCanvasClick);

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
