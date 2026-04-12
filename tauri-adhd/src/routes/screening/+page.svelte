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
  let error = $state(null);
  let loadingSubjects = $state(true);

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

  async function startScreening() {
    if (!selectedSubjectId) return;

    loading = true;
    error = null;
    try {
      resetSession();

      const session = await invoke('start_screening', { subjectId: selectedSubjectId });
      const subject = subjects.find((s) => s.id === selectedSubjectId);

      currentSession.set(session);
      currentSubject.set(subject);
      phase.set(PHASES.CALIBRATING);

      goto('/screening/running');
    } catch (e) {
      error = '启动筛查失败: ' + e;
      console.error(e);
    } finally {
      loading = false;
    }
  }

  let canStart = $derived(selectedSubjectId && !loading);
</script>

<div class="page-content">
  <!-- Hero card -->
  <div class="hero-card animate-fade-in">
    <div class="hero-bg">
      <svg class="hero-icon" width="64" height="64" viewBox="0 0 64 64" fill="none">
        <circle cx="32" cy="32" r="28" fill="rgba(255,255,255,0.15)" />
        <circle cx="32" cy="32" r="16" fill="rgba(255,255,255,0.2)" />
        <circle cx="32" cy="32" r="6" fill="white" />
        <line x1="32" y1="10" x2="32" y2="22" stroke="white" stroke-width="2" opacity="0.5" />
        <line x1="32" y1="42" x2="32" y2="54" stroke="white" stroke-width="2" opacity="0.5" />
        <line x1="10" y1="32" x2="22" y2="32" stroke="white" stroke-width="2" opacity="0.5" />
        <line x1="42" y1="32" x2="54" y2="32" stroke="white" stroke-width="2" opacity="0.5" />
      </svg>
    </div>
    <h1 class="hero-title">视觉记忆挑战</h1>
    <p class="hero-subtitle">Sternberg 工作记忆范式 -- 注意力早期筛查</p>
  </div>

  <!-- Steps guide -->
  <div class="steps-section animate-fade-in">
    <h2 class="section-title">闯关流程</h2>
    <div class="steps">
      <div class="step">
        <div class="step-badge">1</div>
        <div class="step-content">
          <span class="step-title">校准眼动</span>
          <span class="step-desc">跟随屏幕上的圆点点击，让系统熟悉你的眼睛</span>
        </div>
      </div>
      <div class="step">
        <div class="step-badge">2</div>
        <div class="step-content">
          <span class="step-title">视觉记忆闯关</span>
          <span class="step-desc">记住出现的小圆点，判断探测点是否出现过 (共8关)</span>
        </div>
      </div>
      <div class="step">
        <div class="step-badge">3</div>
        <div class="step-content">
          <span class="step-title">查看报告</span>
          <span class="step-desc">系统自动分析注意力表现，生成专业报告</span>
        </div>
      </div>
    </div>
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
      <div class="subject-picker">
        <select class="input-field" bind:value={selectedSubjectId}>
          {#each subjects as subject}
            <option value={subject.id}>{subject.display_name} ({subject.id})</option>
          {/each}
        </select>
      </div>
    {/if}
  </div>

  {#if error}
    <div class="error-msg animate-fade-in">{error}</div>
  {/if}

  <!-- Start button -->
  <div class="start-section">
    <button class="btn-primary start-btn" onclick={startScreening} disabled={!canStart}>
      {#if loading}
        <div class="btn-spinner"></div>
        准备中...
      {:else}
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="5,3 19,12 5,21" fill="currentColor" stroke="none" />
        </svg>
        开始闯关
      {/if}
    </button>
  </div>
</div>

<style>
  /* ── Hero card ─────────────────────────────────────────────── */
  .hero-card {
    background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    border-radius: var(--radius-lg);
    padding: var(--space-2xl) var(--space-lg);
    text-align: center;
    color: white;
    position: relative;
    overflow: hidden;
    margin-bottom: var(--space-xl);
  }
  .hero-bg {
    display: flex;
    justify-content: center;
    margin-bottom: var(--space-md);
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
    background: var(--surface);
    border-radius: var(--radius-md);
    padding: var(--space-md) var(--space-lg);
    box-shadow: var(--shadow-sm);
  }
  .step-badge {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--tertiary);
    color: var(--text);
    font-weight: 800;
    font-size: var(--font-size-lg);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .step-content {
    display: flex;
    flex-direction: column;
    gap: 2px;
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
</style>
