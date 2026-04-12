<script>
  import { invoke } from '@tauri-apps/api/core';
  import { goto } from '$app/navigation';
  import { onMount, onDestroy } from 'svelte';
  import {
    currentSession,
    phase,
    PHASES,
    calibration,
    trialResults,
    totalTrialsCompleted,
    currentBlock,
    currentReport,
    TOTAL_TRIALS,
    TOTAL_BLOCKS,
    accuracy,
    progress,
    sessionError,
  } from '$lib/stores/session.js';
  import CalibrationCanvas from '$lib/components/CalibrationCanvas.svelte';
  import SternbergCanvas from '$lib/components/SternbergCanvas.svelte';

  let currentPhase = $state('idle');
  let trialsCompleted = $state(0);
  let totalProgress = $state(0);
  let currentAcc = $state(0);
  let block = $state(1);
  let error = $state(null);
  let report = $state(null);

  const encouragements = [
    '你做得很棒',
    '继续加油',
    '保持专注',
    '你真厉害',
    '快要完成了',
    '坚持住',
  ];
  let encourageIndex = $state(0);
  let encourageTimer = null;

  const unsubs = [];

  onMount(() => {
    unsubs.push(phase.subscribe((v) => { currentPhase = v; }));
    unsubs.push(totalTrialsCompleted.subscribe((v) => { trialsCompleted = v; }));
    unsubs.push(progress.subscribe((v) => { totalProgress = v; }));
    unsubs.push(accuracy.subscribe((v) => { currentAcc = v; }));
    unsubs.push(currentBlock.subscribe((v) => { block = v; }));
    unsubs.push(sessionError.subscribe((v) => { error = v; }));
    unsubs.push(currentReport.subscribe((v) => { report = v; }));

    encourageTimer = setInterval(() => {
      encourageIndex = (encourageIndex + 1) % encouragements.length;
    }, 4000);
  });

  onDestroy(() => {
    unsubs.forEach((fn) => fn());
    if (encourageTimer) clearInterval(encourageTimer);
  });

  function handleCalibrationDone() {
    phase.set(PHASES.RUNNING);
  }

  function handleCalibrationCancel() {
    goto('/screening');
  }

  async function handleAllTrialsDone(event) {
    phase.set(PHASES.GENERATING_REPORT);

    try {
      let sessionId;
      const unsubSession = currentSession.subscribe((s) => { sessionId = s?.session_id || s?.id; });
      unsubSession();

      let trials;
      const unsubTrials = trialResults.subscribe((t) => { trials = t; });
      unsubTrials();

      const reportResult = await invoke('finish_screening', {
        sessionId: sessionId,
        trials: trials,
      });

      currentReport.set(reportResult);
      phase.set(PHASES.DONE);
      goto('/screening/result');
    } catch (e) {
      console.error('Failed to finish screening:', e);
      sessionError.set(String(e));
      phase.set(PHASES.ERROR);
    }
  }

  function handleExit() {
    goto('/screening');
  }

  // SVG progress ring
  const ringSize = 240;
  const strokeWidth = 16;
  const radius = (ringSize - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  let strokeDashoffset = $derived(circumference * (1 - totalProgress));
</script>

<div class="running-page">
  {#if currentPhase === PHASES.CALIBRATING}
    <CalibrationCanvas
      onDone={handleCalibrationDone}
      onCancel={handleCalibrationCancel}
    />

  {:else if currentPhase === PHASES.RUNNING || currentPhase === PHASES.BREAK}
    <SternbergCanvas onAllDone={handleAllTrialsDone} />

  {:else if currentPhase === PHASES.GENERATING_REPORT}
    <div class="overlay-screen">
      <div class="report-card">
        <!-- Big animated progress ring -->
        <div class="ring-wrapper">
          <svg width={ringSize} height={ringSize} class="ring-svg">
            <circle cx={ringSize/2} cy={ringSize/2} r={radius}
                    fill="none" stroke="#FFE8D1" stroke-width={strokeWidth} />
            <circle cx={ringSize/2} cy={ringSize/2} r={radius}
                    fill="none" stroke="#FF8C42" stroke-width={strokeWidth}
                    stroke-linecap="round"
                    stroke-dasharray={circumference}
                    stroke-dashoffset={strokeDashoffset}
                    transform="rotate(-90 {ringSize/2} {ringSize/2})"
                    class="ring-progress" />
          </svg>
          <div class="ring-center">
            <span class="ring-pct">{Math.round(totalProgress * 100)}%</span>
            <span class="ring-label">已完成</span>
          </div>
        </div>

        <div class="report-spinner"></div>
        <h2 class="report-title">正在分析注意力表现...</h2>
        <p class="report-sub">正在提取 27 个注意力特征</p>

        <!-- Stats row -->
        <div class="stats-row">
          <div class="stat-chip">
            <div class="stat-icon" style="background: rgba(78,205,196,0.15);">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M10 2L12.5 7.5L18 8.5L14 12.5L15 18L10 15L5 18L6 12.5L2 8.5L7.5 7.5L10 2Z"
                      fill="#4ECDC4"/>
              </svg>
            </div>
            <div>
              <div class="stat-label">准确率</div>
              <div class="stat-value">{Math.round(currentAcc * 100)}%</div>
            </div>
          </div>
          <div class="stat-chip">
            <div class="stat-icon" style="background: rgba(255,209,102,0.15);">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <rect x="3" y="10" width="3" height="7" rx="1" fill="#FFD166"/>
                <rect x="8.5" y="6" width="3" height="11" rx="1" fill="#FFD166"/>
                <rect x="14" y="3" width="3" height="14" rx="1" fill="#FFD166"/>
              </svg>
            </div>
            <div>
              <div class="stat-label">试次</div>
              <div class="stat-value">{trialsCompleted}/{TOTAL_TRIALS}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

  {:else if currentPhase === PHASES.ERROR}
    <div class="overlay-screen">
      <div class="error-card">
        <div class="error-icon-ring">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
            <line x1="16" y1="16" x2="32" y2="32" stroke="#E63946" stroke-width="3" stroke-linecap="round"/>
            <line x1="32" y1="16" x2="16" y2="32" stroke="#E63946" stroke-width="3" stroke-linecap="round"/>
          </svg>
        </div>
        <h2 class="error-title">出错了</h2>
        <p class="error-msg">{error || '未知错误'}</p>
        <button class="btn-back" onclick={handleExit}>返回</button>
      </div>
    </div>

  {:else}
    <div class="overlay-screen">
      <div class="report-spinner"></div>
      <p style="margin-top: 20px; color: var(--text-muted);">准备中...</p>
    </div>
  {/if}
</div>

<style>
  .running-page {
    width: 100vw;
    height: 100vh;
    overflow: hidden;
    background: #000;
  }

  .overlay-screen {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #FFF8F0;
  }

  /* ── Report generating ─────────────────────────────────── */

  .report-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 24px;
    padding: 48px 32px;
    max-width: 420px;
    text-align: center;
  }

  .ring-wrapper {
    position: relative;
    width: 240px;
    height: 240px;
  }

  .ring-svg {
    filter: drop-shadow(0 8px 24px rgba(255, 140, 66, 0.2));
  }

  .ring-progress {
    transition: stroke-dashoffset 0.5s ease;
  }

  .ring-center {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }

  .ring-pct {
    font-size: 52px;
    font-weight: 800;
    color: #FF8C42;
    line-height: 1;
  }

  .ring-label {
    font-size: 14px;
    color: #8B6F5C;
    margin-top: 4px;
  }

  .report-spinner {
    width: 32px;
    height: 32px;
    border: 3px solid #FFE0CC;
    border-top-color: #FF8C42;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .report-title {
    font-size: 24px;
    font-weight: 700;
    color: #2B1810;
    margin: 0;
  }

  .report-sub {
    font-size: 15px;
    color: #8B6F5C;
    margin: 0;
  }

  /* Stats chips */
  .stats-row {
    display: flex;
    gap: 16px;
    margin-top: 8px;
  }

  .stat-chip {
    display: flex;
    align-items: center;
    gap: 12px;
    background: #FFFFFF;
    border: 1px solid #F0E0CC;
    border-radius: 20px;
    padding: 14px 20px;
    min-width: 140px;
  }

  .stat-icon {
    width: 44px;
    height: 44px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .stat-label {
    font-size: 12px;
    color: #8B6F5C;
    font-weight: 600;
  }

  .stat-value {
    font-size: 18px;
    font-weight: 700;
    color: #2B1810;
  }

  /* ── Error screen ───────────────────────────────────────── */

  .error-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    padding: 48px 32px;
    max-width: 380px;
    text-align: center;
  }

  .error-icon-ring {
    width: 88px;
    height: 88px;
    border-radius: 50%;
    background: rgba(230, 57, 70, 0.1);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .error-title {
    font-size: 24px;
    font-weight: 700;
    color: #E63946;
    margin: 0;
  }

  .error-msg {
    font-size: 14px;
    color: #8B6F5C;
    margin: 0;
    word-break: break-all;
  }

  .btn-back {
    margin-top: 12px;
    background: #FF8C42;
    color: white;
    border: none;
    border-radius: 28px;
    padding: 14px 36px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .btn-back:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(255, 140, 66, 0.35);
  }
</style>
