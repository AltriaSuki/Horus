<script>
  import { invoke } from '@tauri-apps/api/core';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import {
    currentSession,
    currentSubject,
    phase,
    PHASES,
    resetSession,
  } from '$lib/stores/session.js';

  let subjects = $state([]);
  let selectedSubjectId = $state('');
  let loading = $state(false);
  let loadingMsg = $state('');
  let error = $state(null);
  let loadingSubjects = $state(true);
  // 'idle' | 'permission' — show camera permission explanation modal
  let stage = $state('idle');

  onMount(async () => {
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
  });

  function requestStart() {
    if (!selectedSubjectId) return;
    error = null;
    stage = 'permission';
  }

  function cancelStart() {
    stage = 'idle';
    error = null;
  }

  async function confirmAndStart() {
    loading = true;
    error = null;
    loadingMsg = '正在启动摄像头...';
    try {
      // 直接启动早筛 — 首次调用会触发 macOS 权限弹框。避免单独的预探测以
      // 防止"打开→关闭→再打开"期间 AVFoundation 未及时释放导致第二次失败。
      resetSession();
      const session = await invoke('start_screening', {
        subjectId: selectedSubjectId,
        screenWidth: window.innerWidth,
        screenHeight: window.innerHeight,
      });
      const subject = subjects.find((s) => s.id === selectedSubjectId);

      currentSession.set(session);
      currentSubject.set(subject);
      phase.set(PHASES.CALIBRATING);

      goto('/screening/running');
    } catch (e) {
      const msg = typeof e === 'string' ? e : (e?.message ?? String(e));
      error = msg;
      console.error(e);
      // 留在 permission 阶段让用户看到错误并选择重试/取消
    } finally {
      loading = false;
      loadingMsg = '';
    }
  }

  let canStart = $derived(selectedSubjectId && !loading);
</script>

<div class="page-content">
  <!-- Hero card -->
  <div class="hero-card animate-fade-in">
    <div class="hero-bg animate-float">
      <svg class="hero-icon" width="80" height="80" viewBox="0 0 80 80" fill="none">
        <!-- Glowing Background -->
        <circle cx="40" cy="40" r="40" fill="rgba(255,255,255,0.2)"/>
        <!-- Rocket Body -->
        <path d="M40 15C40 15 25 35 25 55C25 60 30 65 40 65C50 65 55 60 55 55C55 35 40 15 40 15Z" fill="white"/>
        <!-- Window -->
        <circle cx="40" cy="40" r="6" fill="var(--primary-dark)"/>
        <!-- Fins -->
        <path d="M25 55L15 65H30L25 55Z" fill="white"/>
        <path d="M55 55L65 65H50L55 55Z" fill="white"/>
        <!-- Flame -->
        <path d="M35 65 Q40 75 45 65 Z" fill="#FFE699"/>
      </svg>
    </div>
    <h1 class="hero-title">视觉记忆挑战</h1>
    <p class="hero-subtitle">Sternberg 工作记忆范式 -- 注意力早期筛查</p>
  </div>

  <!-- Steps guide -->
  <div class="steps-section">
    <h2 class="section-title animate-fade-in">闯关流程</h2>
    <div class="steps">
      <div class="step animate-fade-in stagger-1">
        <div class="step-badge">1</div>
        <div class="step-content">
          <span class="step-title">校准眼动</span>
          <span class="step-desc">跟随屏幕上的圆点点击，让系统熟悉你的眼睛</span>
        </div>
      </div>
      <div class="step animate-fade-in stagger-2">
        <div class="step-badge">2</div>
        <div class="step-content">
          <span class="step-title">视觉记忆闯关</span>
          <span class="step-desc">记住出现的小圆点，判断探测点是否出现过 (共8关)</span>
        </div>
      </div>
      <div class="step animate-fade-in stagger-3">
        <div class="step-badge">3</div>
        <div class="step-content">
          <span class="step-title">查看报告</span>
          <span class="step-desc">系统自动分析注意力表现，生成专业报告</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Subject picker -->
  <div class="picker-section animate-fade-in stagger-4">
    <h2 class="section-title">选择被试</h2>
    {#if loadingSubjects}
      <p class="text-muted">加载中...</p>
    {:else if subjects.length === 0}
      <div class="no-subjects card">
        <p>还没有被试，请先添加</p>
        <a href="/subjects/create" class="btn-secondary" style="margin-top: var(--space-md);">添加被试</a>
      </div>
    {:else}
      <div class="subject-picker">
        <select class="input-field" bind:value={selectedSubjectId}>
          {#each subjects as subject}
            <option value={subject.id}>{subject.display_name} ({subject.id})</option>
          {/each}
        </select>
      </div>
    {/if}
  </div>

  {#if error && stage === 'idle'}
    <div class="error-msg animate-fade-in">{error}</div>
  {/if}

  <!-- Start button -->
  <div class="start-section">
    <button class="btn-primary start-btn" onclick={requestStart} disabled={!canStart}>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="5,3 19,12 5,21" fill="currentColor" stroke="none" />
      </svg>
      开始闯关
    </button>
  </div>
</div>

{#if stage === 'permission'}
  <div class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="perm-title">
    <div class="modal-card animate-fade-in">
      <div class="modal-icon">
        <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M23 7l-7 5 7 5V7z" />
          <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
        </svg>
      </div>
      <h2 id="perm-title" class="modal-title">需要摄像头权限</h2>
      <p class="modal-body">
        本应用需要访问摄像头来跟踪你的眼睛运动，用于注意力评估。
        <br /><br />
        摄像头画面<strong>只在本机处理</strong>，不会上传到任何服务器，也不会被保存。
      </p>

      {#if error}
        <div class="modal-error">
          <strong>无法访问摄像头</strong>
          <pre>{error}</pre>
        </div>
      {/if}

      {#if loading}
        <div class="modal-loading">
          <div class="btn-spinner"></div>
          <span>{loadingMsg}</span>
        </div>
      {:else}
        <div class="modal-actions">
          <button class="btn-ghost" onclick={cancelStart}>取消</button>
          <button class="btn-primary" onclick={confirmAndStart}>
            {error ? '重试' : '同意并开始'}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  /* ── Hero card ─────────────────────────────────────────────── */
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
  }

  /* ── Steps ─────────────────────────────────────────────────── */
  .steps-section {
    margin-bottom: var(--space-xl);
  }
  .steps {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
  }
  .step {
    display: flex;
    align-items: flex-start;
    gap: var(--space-md);
    background: var(--surface-solid);
    border-radius: var(--radius-lg);
    border: 3px solid rgba(255, 255, 255, 0.6);
    padding: var(--space-md) var(--space-lg);
    box-shadow: var(--shadow-sm);
    transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.4s;
  }
  .step:hover {
    transform: translateY(-4px) scale(1.02);
    box-shadow: var(--shadow-md);
  }
  .step-badge {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: var(--tertiary);
    color: white;
    font-weight: 800;
    font-size: var(--font-size-xl);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    box-shadow: 0 4px 12px rgba(164, 222, 255, 0.4);
  }
  .step-content {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .step-title {
    font-weight: 700;
    font-size: var(--font-size-base);
    color: var(--text);
  }
  .step-desc {
    font-size: var(--font-size-sm);
    color: var(--text-muted);
  }

  /* ── Picker ────────────────────────────────────────────────── */
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

  /* ── Start ─────────────────────────────────────────────────── */
  .start-section {
    display: flex;
    justify-content: center;
    padding: var(--space-md) 0;
  }
  .start-btn {
    font-size: var(--font-size-xl);
    padding: 18px 48px;
  }
  .btn-spinner {
    width: 20px;
    height: 20px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  .error-msg {
    color: var(--error);
    font-size: var(--font-size-sm);
    padding: var(--space-sm) var(--space-md);
    background: #FFE0E0;
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-md);
  }

  /* ── Permission modal ──────────────────────────────────────── */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(43, 24, 16, 0.55);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
    padding: var(--space-md);
  }
  .modal-card {
    background: var(--surface-solid, #FFF8F0);
    border-radius: var(--radius-xl);
    padding: var(--space-xl);
    max-width: 480px;
    width: 100%;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.25);
    border: 4px solid rgba(255, 140, 66, 0.3);
    text-align: center;
  }
  .modal-icon {
    width: 80px;
    height: 80px;
    margin: 0 auto var(--space-md);
    border-radius: 50%;
    background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
  }
  .modal-title {
    font-size: var(--font-size-xl);
    font-weight: 800;
    color: var(--text);
    margin-bottom: var(--space-md);
  }
  .modal-body {
    font-size: var(--font-size-base);
    color: var(--text-muted);
    line-height: 1.6;
    text-align: left;
    margin-bottom: var(--space-md);
  }
  .modal-body strong {
    color: var(--primary-dark);
  }
  .modal-error {
    background: #FFE0E0;
    border-radius: var(--radius-sm);
    padding: var(--space-sm) var(--space-md);
    margin-bottom: var(--space-md);
    text-align: left;
  }
  .modal-error strong {
    display: block;
    color: var(--error);
    margin-bottom: 4px;
  }
  .modal-error pre {
    font-size: var(--font-size-sm);
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
    font-family: inherit;
  }
  .modal-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-sm);
    color: var(--text-muted);
    padding: var(--space-md) 0;
  }
  .modal-actions {
    display: flex;
    gap: var(--space-md);
    justify-content: center;
  }
  .btn-ghost {
    background: transparent;
    color: var(--text-muted);
    border: 2px solid rgba(0, 0, 0, 0.1);
    padding: 10px 24px;
    border-radius: var(--radius-md);
    font-weight: 600;
    font-size: var(--font-size-base);
    cursor: pointer;
    transition: all 0.2s;
  }
  .btn-ghost:hover {
    background: rgba(0, 0, 0, 0.04);
    border-color: rgba(0, 0, 0, 0.2);
  }
</style>
