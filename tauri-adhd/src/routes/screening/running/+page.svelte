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

  // Encouraging messages
  const encouragements = [
    '你做得很棒',
    '继续加油',
    '集中注意力',
    '保持专注',
    '马上就好了',
    '你真厉害',
    '快要完成了',
    '坚持住',
  ];
  let encourageIndex = $state(0);
  let encourageTimer = null;

  // Subscribe to stores
  const unsubs = [];

  onMount(() => {
    unsubs.push(phase.subscribe((v) => { currentPhase = v; }));
    unsubs.push(totalTrialsCompleted.subscribe((v) => { trialsCompleted = v; }));
    unsubs.push(progress.subscribe((v) => { totalProgress = v; }));
    unsubs.push(accuracy.subscribe((v) => { currentAcc = v; }));
    unsubs.push(currentBlock.subscribe((v) => { block = v; }));
    unsubs.push(sessionError.subscribe((v) => { error = v; }));
    unsubs.push(currentReport.subscribe((v) => { report = v; }));

    // Rotate encouragements
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
      sessionError.set('生成报告失败: ' + e);
      phase.set(PHASES.ERROR);
    }
  }

  function handleExit() {
    goto('/screening');
  }

  // Progress ring properties
  const ringSize = 200;
  const strokeWidth = 12;
  const radius = (ringSize - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  let strokeDashoffset = $derived(circumference * (1 - totalProgress));

  let phaseBannerText = $derived(
    currentPhase === PHASES.CALIBRATING ? '校准眼动追踪'
    : currentPhase === PHASES.RUNNING ? '视觉记忆闯关中'
    : currentPhase === PHASES.GENERATING_REPORT ? '正在生成报告...'
    : currentPhase === PHASES.ERROR ? '发生错误'
    : '准备中'
  );
</script>

<div class="running-page">
  {#if currentPhase === PHASES.CALIBRATING}
    <CalibrationCanvas onDone={handleCalibrationDone} />
  {:else if currentPhase === PHASES.RUNNING || currentPhase === PHASES.BREAK}
    <SternbergCanvas onAllDone={handleAllTrialsDone} />
  {:else if currentPhase === PHASES.GENERATING_REPORT}
    <div class="overlay-screen">
      <div class="report-generating">
        <!-- Progress ring -->
        <div class="progress-ring-wrapper">
          <svg width={ringSize} height={ringSize} class="progress-ring">
            <circle
              cx={ringSize / 2}
              cy={ringSize / 2}
              r={radius}
              fill="none"
              stroke="rgba(255,255,255,0.1)"
              stroke-width={strokeWidth}
            />
            <circle
              cx={ringSize / 2}
              cy={ringSize / 2}
              r={radius}
              fill="none"
              stroke="var(--secondary)"
              stroke-width={strokeWidth}
              stroke-linecap="round"
              stroke-dasharray={circumference}
              stroke-dashoffset={strokeDashoffset}
              transform="rotate(-90 {ringSize / 2} {ringSize / 2})"
            />
          </svg>
          <div class="ring-center-text">
            <span class="ring-percent">{Math.round(totalProgress * 100)}%</span>
          </div>
        </div>

        <div class="generating-spinner"></div>
        <h2 class="generating-text">正在分析注意力表现...</h2>
        <p class="generating-sub">请稍候，这可能需要几秒钟</p>
      </div>
    </div>
  {:else if currentPhase === PHASES.ERROR}
    <div class="overlay-screen">
      <div class="error-screen">
        <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
          <circle cx="32" cy="32" r="28" fill="var(--error)" opacity="0.15" />
          <circle cx="32" cy="32" r="20" fill="var(--error)" opacity="0.3" />
          <line x1="24" y1="24" x2="40" y2="40" stroke="var(--error)" stroke-width="3" stroke-linecap="round" />
          <line x1="40" y1="24" x2="24" y2="40" stroke="var(--error)" stroke-width="3" stroke-linecap="round" />
        </svg>
        <h2 class="error-title">出错了</h2>
        <p class="error-detail">{error || '未知错误'}</p>
        <button class="btn-primary" onclick={handleExit}>返回</button>
      </div>
    </div>
  {:else}
    <!-- Idle / transition state -->
    <div class="overlay-screen">
      <div class="generating-spinner"></div>
      <p>准备中...</p>
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
    background: var(--bg);
  }

  /* Generating report */
  .report-generating {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-lg);
    text-align: center;
    padding: var(--space-2xl);
  }
  .progress-ring-wrapper {
    position: relative;
    width: 200px;
    height: 200px;
  }
  .progress-ring {
    transform: rotate(0deg);
  }
  .ring-center-text {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .ring-percent {
    font-size: var(--font-size-2xl);
    font-weight: 800;
    color: var(--text);
  }
  .generating-spinner {
    width: 40px;
    height: 40px;
    border: 4px solid #E8DDD4;
    border-top-color: var(--primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  .generating-text {
    font-size: var(--font-size-xl);
    font-weight: 700;
    color: var(--text);
  }
  .generating-sub {
    font-size: var(--font-size-base);
    color: var(--text-muted);
  }

  /* Error screen */
  .error-screen {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-md);
    text-align: center;
    padding: var(--space-2xl);
  }
  .error-title {
    font-size: var(--font-size-xl);
    font-weight: 700;
    color: var(--error);
  }
  .error-detail {
    font-size: var(--font-size-base);
    color: var(--text-muted);
    max-width: 400px;
  }
</style>
