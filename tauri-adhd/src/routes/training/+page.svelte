<script>
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';
  import { onMount, onDestroy } from 'svelte';
  import TrainingCalibrationCanvas from '$lib/components/TrainingCalibrationCanvas.svelte';

  let subjects = $state([]);
  let selectedSubjectId = $state('');
  let loadingSubjects = $state(true);
  let launching = $state(false);
  let gamePid = $state(null);
  let error = $state(null);

  // Eye tracker state: 'stopped' | 'calibrating' | 'calibrated' | 'serving' | 'error'
  let eyeTrackerStatus = $state('stopped');
  let eyeTrackerStarting = $state(false);
  let eyeTrackerLogs = $state([]);
  // Whether to show the fullscreen calibration canvas
  let showCalibrationCanvas = $state(false);

  // Gaze monitor
  let gazeCanvas = $state(null);
  let gazeX = $state(0);
  let gazeY = $state(0);
  let gazeValid = $state(false);
  let unlistenGaze = null;
  let unlistenEyeStatus = null;
  let unlistenEyeLog = null;

  onMount(async () => {
    // Load subjects
    try {
      subjects = await invoke('list_subjects');
      if (subjects.length > 0) {
        selectedSubjectId = subjects[0].id;
      }
    } catch (e) {
      console.error('Failed to load subjects:', e);
    } finally {
      loadingSubjects = false;
    }

    // Check if eye tracker is already running
    try {
      eyeTrackerStatus = await invoke('get_eye_tracker_status');
    } catch (e) {
      console.error('Failed to get eye tracker status:', e);
    }

    // Listen for gaze frames
    unlistenGaze = await listen('gaze_frame', (event) => {
      const { x, y, valid } = event.payload;
      gazeX = x;
      gazeY = y;
      gazeValid = valid;
      drawGaze();
    });

    // Listen for eye tracker status changes
    unlistenEyeStatus = await listen('eye_tracker_status', (event) => {
      eyeTrackerStatus = event.payload;
      eyeTrackerStarting = false;
    });

    // Listen for eye tracker log messages
    unlistenEyeLog = await listen('eye_tracker_log', (event) => {
      eyeTrackerLogs = [...eyeTrackerLogs.slice(-49), event.payload];
    });
  });

  onDestroy(() => {
    if (unlistenGaze) unlistenGaze();
    if (unlistenEyeStatus) unlistenEyeStatus();
    if (unlistenEyeLog) unlistenEyeLog();
  });

  function drawGaze() {
    if (!gazeCanvas) return;
    const ctx = gazeCanvas.getContext('2d');
    const w = gazeCanvas.width;
    const h = gazeCanvas.height;

    ctx.fillStyle = 'rgba(20, 20, 30, 0.3)';
    ctx.fillRect(0, 0, w, h);

    if (gazeValid) {
      const cx = (gazeX / window.innerWidth) * w;
      const cy = (gazeY / window.innerHeight) * h;

      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, 12);
      grad.addColorStop(0, 'rgba(78, 205, 196, 0.8)');
      grad.addColorStop(1, 'rgba(78, 205, 196, 0)');
      ctx.beginPath();
      ctx.arc(cx, cy, 12, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(cx, cy, 3, 0, Math.PI * 2);
      ctx.fillStyle = '#4ECDC4';
      ctx.fill();
    }
  }

  async function startEyeTracker() {
    eyeTrackerStarting = true;
    error = null;
    eyeTrackerLogs = [];
    try {
      // Start Python process in headless mode (no pygame window)
      await invoke('start_eye_tracker', { headless: true });
      eyeTrackerStatus = 'calibrating';
      // Show the in-app calibration canvas
      showCalibrationCanvas = true;
    } catch (e) {
      const msg = typeof e === 'string' ? e : (e?.message ?? String(e));
      error = msg;
      eyeTrackerStarting = false;
    }
  }

  function handleCalibrationDone() {
    showCalibrationCanvas = false;
    eyeTrackerStatus = 'calibrated';
    eyeTrackerStarting = false;
  }

  function handleCalibrationCancel() {
    showCalibrationCanvas = false;
    // Stop the Python process since calibration was cancelled
    invoke('stop_eye_tracker').catch(() => {});
    eyeTrackerStatus = 'stopped';
    eyeTrackerStarting = false;
  }

  async function stopEyeTracker() {
    try {
      await invoke('stop_eye_tracker');
      eyeTrackerStatus = 'stopped';
    } catch (e) {
      console.error('Failed to stop eye tracker:', e);
    }
  }

  async function launchGame() {
    if (!selectedSubjectId) return;
    launching = true;
    error = null;
    try {
      const pid = await invoke('launch_game', {
        gamePath: '',
        screenWidth: window.innerWidth,
        screenHeight: window.innerHeight,
      });
      gamePid = pid;
    } catch (e) {
      const msg = typeof e === 'string' ? e : (e?.message ?? String(e));
      error = msg;
      console.error(e);
    } finally {
      launching = false;
    }
  }

  async function stopGame() {
    try {
      await invoke('stop_game');
      gamePid = null;
      eyeTrackerStatus = 'stopped';
    } catch (e) {
      console.error('Failed to stop game:', e);
    }
  }

  // Derived: whether eye tracker is ready (calibrated or serving)
  let eyeTrackerReady = $derived(
    eyeTrackerStatus === 'calibrated' || eyeTrackerStatus === 'serving'
  );
</script>

{#if showCalibrationCanvas}
  <TrainingCalibrationCanvas
    onDone={handleCalibrationDone}
    onCancel={handleCalibrationCancel}
  />
{/if}

<div class="page-content">
  <!-- Hero card -->
  <div class="hero-card animate-fade-in">
    <div class="hero-bg animate-float">
      <svg class="hero-icon" width="80" height="80" viewBox="0 0 80 80" fill="none">
        <circle cx="40" cy="40" r="40" fill="rgba(255,255,255,0.2)"/>
        <!-- Gamepad body -->
        <rect x="15" y="25" width="50" height="30" rx="15" fill="white"/>
        <!-- D-pad -->
        <rect x="23" y="36" width="10" height="4" rx="1" fill="var(--primary-dark)"/>
        <rect x="26" y="33" width="4" height="10" rx="1" fill="var(--primary-dark)"/>
        <!-- Buttons -->
        <circle cx="50" cy="42" r="4" fill="var(--primary-dark)"/>
        <circle cx="58" cy="35" r="4" fill="var(--primary-dark)"/>
        <!-- Smile -->
        <path d="M35 45 Q 40 48 45 45" stroke="var(--primary-dark)" stroke-width="2.5" stroke-linecap="round" fill="none"/>
      </svg>
    </div>
    <h1 class="hero-title">注意力训练</h1>
    <p class="hero-subtitle">通过眼控互动游戏提升专注力与工作记忆</p>
  </div>

  <!-- Subject picker -->
  <div class="picker-section animate-fade-in">
    <h2 class="section-title">选择被试</h2>
    {#if loadingSubjects}
      <p class="text-muted">加载中...</p>
    {:else if subjects.length === 0}
      <div class="no-subjects card">
        <p>还没有被试，请先添加</p>
        <a href="/subjects/create" class="btn-secondary" style="margin-top: var(--space-md);">添加被试</a>
      </div>
    {:else}
      <select class="input-field" bind:value={selectedSubjectId}>
        {#each subjects as subject}
          <option value={subject.id}>{subject.display_name} ({subject.id})</option>
        {/each}
      </select>
    {/if}
  </div>

  {#if error}
    <div class="error-msg">{error}</div>
  {/if}

  <!-- Step 1: Calibrate eye tracker -->
  <div class="step-section animate-fade-in">
    <h2 class="section-title">
      <span class="step-badge" class:done={eyeTrackerReady}>1</span>
      校准眼动追踪
    </h2>
    <p class="step-desc">点击开始校准后，请依次注视每个校准点并点击。校准完成后即可启动游戏。</p>

    <div class="step-actions">
      {#if eyeTrackerStatus === 'stopped' || eyeTrackerStatus === 'error'}
        <button class="btn-primary calibrate-btn" onclick={startEyeTracker} disabled={eyeTrackerStarting}>
          {#if eyeTrackerStarting}
            <div class="btn-spinner"></div>
            启动中...
          {:else}
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <circle cx="12" cy="12" r="10"/>
              <circle cx="12" cy="12" r="3"/>
              <line x1="12" y1="2" x2="12" y2="5"/>
              <line x1="12" y1="19" x2="12" y2="22"/>
              <line x1="2" y1="12" x2="5" y2="12"/>
              <line x1="19" y1="12" x2="22" y2="12"/>
            </svg>
            开始校准
          {/if}
        </button>
      {:else if eyeTrackerStatus === 'calibrating' || eyeTrackerStatus === 'ready'}
        <div class="status-pill calibrating">
          <div class="btn-spinner"></div>
          校准中…
        </div>
        <button class="btn-text" onclick={stopEyeTracker}>取消</button>
      {:else}
        <div class="status-pill ready">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="20,6 9,17 4,12"/>
          </svg>
          校准完成，眼动服务已就绪
        </div>
        <button class="btn-text" onclick={startEyeTracker}>重新校准</button>
      {/if}
    </div>

    {#if eyeTrackerLogs.length > 0}
      <details class="log-details">
        <summary>查看日志 ({eyeTrackerLogs.length})</summary>
        <pre class="log-pre">{eyeTrackerLogs.join('\n')}</pre>
      </details>
    {/if}
  </div>

  <!-- Step 2: Launch game -->
  <div class="step-section animate-fade-in">
    <h2 class="section-title">
      <span class="step-badge" class:done={gamePid}>2</span>
      启动训练游戏
    </h2>
    <p class="step-desc">游戏启动后，按 F2 切换为眼动控制模式，用眼睛控制角色。</p>

    <div class="step-actions">
      {#if gamePid}
        <button class="btn-secondary stop-btn" onclick={stopGame}>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <rect x="4" y="4" width="12" height="12" rx="2" />
          </svg>
          停止游戏
        </button>
      {:else}
        <button class="btn-primary launch-btn" onclick={launchGame}
          disabled={!selectedSubjectId || launching || !eyeTrackerReady}>
          {#if launching}
            <div class="btn-spinner"></div>
            启动中...
          {:else}
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="5,3 19,12 5,21" fill="currentColor" stroke="none" />
            </svg>
            开始训练
          {/if}
        </button>
        {#if !eyeTrackerReady && !launching}
          <p class="hint-text">请先完成眼动校准</p>
        {/if}
      {/if}
    </div>
  </div>

  <!-- Gaze monitor -->
  <div class="gaze-section animate-fade-in">
    <h2 class="section-title">实时注视监控</h2>
    <div class="gaze-monitor card">
      <canvas
        bind:this={gazeCanvas}
        width="320"
        height="180"
        class="gaze-canvas"
      ></canvas>
      <div class="gaze-status">
        <span class="gaze-dot" class:active={gazeValid}></span>
        <span class="gaze-label">{gazeValid ? '追踪中' : '等待眼动数据...'}</span>
      </div>
    </div>
  </div>
</div>

<style>
  /* Hero */
  .hero-card {
    background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    border-radius: var(--radius-xl);
    padding: var(--space-2xl) var(--space-lg);
    text-align: center;
    color: white;
    position: relative;
    overflow: hidden;
    margin-bottom: var(--space-xl);
    box-shadow: var(--shadow-lg);
    border: 4px solid rgba(255, 255, 255, 0.4);
  }
  .hero-bg {
    display: flex;
    justify-content: center;
    margin-bottom: var(--space-lg);
  }
  .hero-title {
    font-size: var(--font-size-2xl);
    font-weight: 800;
    margin-bottom: var(--space-sm);
  }
  .hero-subtitle {
    font-size: var(--font-size-base);
    opacity: 0.85;
    margin-bottom: var(--space-md);
  }
  .coming-soon-badge {
    display: inline-block;
    padding: 4px 16px;
    background: rgba(255,255,255,0.25);
    border-radius: var(--radius-pill);
    font-size: var(--font-size-sm);
    font-weight: 700;
  }

  /* Picker */
  .picker-section {
    margin-bottom: var(--space-xl);
  }
  .no-subjects {
    text-align: center;
    padding: var(--space-xl);
    color: var(--text-muted);
  }
  .text-muted {
    color: var(--text-muted);
  }

  /* Launch */
  .launch-section {
    display: flex;
    justify-content: center;
    margin-bottom: var(--space-xl);
  }
  .launch-btn {
    font-size: var(--font-size-xl);
    padding: 18px 48px;
  }
  .stop-btn {
    font-size: var(--font-size-lg);
    background: var(--error);
  }
  .btn-spinner {
    width: 20px;
    height: 20px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  /* Steps */
  .step-section {
    margin-bottom: var(--space-xl);
    padding: var(--space-lg);
    background: var(--surface-solid);
    border-radius: var(--radius-lg);
    border: 3px solid rgba(255, 255, 255, 0.6);
  }
  .step-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--text-muted);
    color: white;
    font-size: var(--font-size-sm);
    font-weight: 700;
    margin-right: var(--space-sm);
    transition: background 0.3s;
  }
  .step-badge.done {
    background: var(--secondary, #4ECDC4);
  }
  .step-desc {
    color: var(--text-muted);
    font-size: var(--font-size-sm);
    margin-bottom: var(--space-md);
    line-height: 1.5;
  }
  .step-actions {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    flex-wrap: wrap;
  }
  .calibrate-btn {
    font-size: var(--font-size-base);
    padding: 12px 28px;
  }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-sm);
    padding: 8px 18px;
    border-radius: var(--radius-pill);
    font-size: var(--font-size-sm);
    font-weight: 600;
  }
  .status-pill.calibrating {
    background: rgba(255, 193, 7, 0.15);
    color: #FFC107;
  }
  .status-pill.ready {
    background: rgba(78, 205, 196, 0.15);
    color: #4ECDC4;
  }
  .btn-text {
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: var(--font-size-sm);
    text-decoration: underline;
    padding: 4px 8px;
  }
  .btn-text:hover {
    color: var(--text-primary);
  }
  .hint-text {
    color: var(--text-muted);
    font-size: var(--font-size-sm);
    font-style: italic;
  }
  .log-details {
    margin-top: var(--space-md);
  }
  .log-details summary {
    cursor: pointer;
    color: var(--text-muted);
    font-size: var(--font-size-sm);
  }
  .log-pre {
    max-height: 160px;
    overflow-y: auto;
    background: #1A1A2E;
    color: #ccc;
    padding: var(--space-sm);
    border-radius: var(--radius-sm);
    font-size: 12px;
    line-height: 1.4;
    margin-top: var(--space-sm);
  }

  .error-msg {
    color: var(--error);
    font-size: var(--font-size-sm);
    padding: var(--space-sm) var(--space-md);
    background: #FFE0E0;
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-md);
  }

  /* Gaze monitor */
  .gaze-section {
    margin-bottom: var(--space-xl);
  }
  .gaze-monitor {
    padding: var(--space-md);
    background: var(--surface-solid);
    border-radius: var(--radius-lg);
    border: 3px solid rgba(255, 255, 255, 0.6);
  }
  .gaze-canvas {
    width: 100%;
    height: auto;
    border-radius: var(--radius-md);
    background: #1A1A2E;
    display: block;
    box-shadow: inset 0 4px 12px rgba(0,0,0,0.2);
  }
  .gaze-status {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    margin-top: var(--space-sm);
  }
  .gaze-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
    transition: background 0.3s;
  }
  .gaze-dot.active {
    background: var(--secondary);
    box-shadow: 0 0 6px var(--secondary);
  }
  .gaze-label {
    font-size: var(--font-size-sm);
    color: var(--text-muted);
  }
</style>
