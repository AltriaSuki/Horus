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
    background-image: repeating-linear-gradient(
      45deg,
      rgba(255, 255, 255, 0.05),
      rgba(255, 255, 255, 0.05) 20px,
      transparent 20px,
      transparent 40px
    ), linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    border-radius: var(--radius-xl);
    padding: var(--space-2xl) var(--space-lg);
    text-align: center;
    color: white;
    position: relative;
    overflow: hidden;
    margin-bottom: var(--space-xl);
    box-shadow: var(--shadow-lg);
    border: 4px solid #FFFFFF;
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
</style>
