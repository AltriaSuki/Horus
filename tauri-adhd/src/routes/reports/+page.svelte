<script>
  import { invoke } from '@tauri-apps/api/core';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { currentReport } from '$lib/stores/session.js';

  let subjects = $state([]);
  let selectedSubjectId = $state('');
  let sessions = $state([]);
  let loadingSubjects = $state(true);
  let loadingSessions = $state(false);
  let error = $state(null);

  onMount(async () => {
    try {
      subjects = await invoke('list_subjects');
      if (subjects.length > 0) {
        selectedSubjectId = subjects[0].id;
        await loadSessions();
      }
    } catch (e) {
      console.error('Failed to load subjects:', e);
    } finally {
      loadingSubjects = false;
    }
  });

  async function loadSessions() {
    if (!selectedSubjectId) {
      sessions = [];
      return;
    }
    loadingSessions = true;
    error = null;
    try {
      sessions = await invoke('list_subject_sessions', { subjectId: selectedSubjectId });
    } catch (e) {
      error = '加载历史记录失败: ' + e;
      console.error(e);
    } finally {
      loadingSessions = false;
    }
  }

  async function handleSubjectChange() {
    await loadSessions();
  }

  async function viewReport(session) {
    const sessionId = session.session_id || session.id;
    // Try to load the report
    try {
      const report = await invoke('get_report', { sessionId });
      if (report) {
        currentReport.set(report);
        goto('/screening/result?session_id=' + sessionId);
      } else {
        // No report available
        error = '该次筛查尚未生成报告';
      }
    } catch (e) {
      error = '加载报告失败: ' + e;
    }
  }

  function statusBadgeClass(status) {
    if (!status) return '';
    const s = status.toLowerCase();
    if (s === 'completed' || s === 'done') return 'badge-success';
    if (s === 'failed' || s === 'error') return 'badge-error';
    if (s === 'cancelled' || s === 'canceled') return 'badge-warning';
    return 'badge-default';
  }

  function statusLabel(status) {
    if (!status) return '未知';
    const s = status.toLowerCase();
    if (s === 'completed' || s === 'done') return '已完成';
    if (s === 'failed' || s === 'error') return '失败';
    if (s === 'cancelled' || s === 'canceled') return '已取消';
    if (s === 'running' || s === 'in_progress') return '进行中';
    return status;
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      return d.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return dateStr;
    }
  }
</script>

<div class="page-content">
  <h1 class="section-title">筛查历史</h1>

  <!-- Subject picker -->
  <div class="picker-section">
    {#if loadingSubjects}
      <p class="text-muted">加载中...</p>
    {:else if subjects.length === 0}
      <div class="no-subjects card">
        <p>还没有被试，请先添加</p>
        <a href="/subjects/create" class="btn-secondary" style="margin-top: var(--space-md);">添加被试</a>
      </div>
    {:else}
      <select
        class="input-field"
        bind:value={selectedSubjectId}
        onchange={handleSubjectChange}
      >
        {#each subjects as subject}
          <option value={subject.id}>{subject.display_name} ({subject.id})</option>
        {/each}
      </select>
    {/if}
  </div>

  {#if error}
    <div class="error-msg">{error}</div>
  {/if}

  <!-- Sessions list -->
  <div class="sessions-section animate-fade-in">
    {#if loadingSessions}
      <div class="loading-state">
        <div class="spinner"></div>
        <p>加载中...</p>
      </div>
    {:else if sessions.length === 0}
      <div class="empty-state">
        <svg width="120" height="120" viewBox="0 0 120 120" fill="none">
          <!-- Glowing background -->
          <circle cx="60" cy="60" r="50" fill="rgba(255,255,255,0.4)"/>
          <!-- Bear face -->
          <circle cx="60" cy="50" r="30" fill="var(--primary)"/>
          <circle cx="40" cy="30" r="12" fill="var(--primary)"/>
          <circle cx="80" cy="30" r="12" fill="var(--primary)"/>
          <circle cx="50" cy="45" r="4" fill="white"/>
          <circle cx="70" cy="45" r="4" fill="white"/>
          <path d="M55 52 Q 60 56 65 52" stroke="white" stroke-width="3" stroke-linecap="round" fill="none"/>
          
          <!-- Report -->
          <rect x="35" y="60" width="50" height="45" rx="6" fill="white" stroke="var(--primary-dark)" stroke-width="3"/>
          <line x1="45" y1="75" x2="75" y2="75" stroke="var(--primary-light)" stroke-width="4" stroke-linecap="round"/>
          <line x1="45" y1="85" x2="65" y2="85" stroke="var(--primary-light)" stroke-width="4" stroke-linecap="round"/>
          
          <!-- Paws holding report -->
          <circle cx="35" cy="80" r="8" fill="var(--primary-dark)"/>
          <circle cx="85" cy="80" r="8" fill="var(--primary-dark)"/>
        </svg>
        <h2 class="empty-title">暂无筛查记录</h2>
        <p class="empty-desc">完成一次筛查后，记录将在这里显示</p>
      </div>
    {:else}
      <div class="sessions-list">
        {#each sessions as session, i}
          <button class="session-card card" onclick={() => viewReport(session)}>
            <div class="session-header">
              <span class="session-num">第 {sessions.length - i} 次筛查</span>
              <span class="badge {statusBadgeClass(session.status)}">
                {statusLabel(session.status)}
              </span>
            </div>
            <div class="session-meta">
              <span class="session-date">{formatDate(session.created_at)}</span>
              {#if session.task_type}
                <span class="session-type">{session.task_type}</span>
              {/if}
            </div>
            <div class="session-arrow">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M6 3l5 5-5 5" />
              </svg>
            </div>
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
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

  .error-msg {
    color: var(--error);
    font-size: var(--font-size-sm);
    padding: var(--space-sm) var(--space-md);
    background: #FFE0E0;
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-md);
  }

  /* Loading */
  .loading-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-md);
    padding: var(--space-2xl);
    color: var(--text-muted);
  }
  .spinner {
    width: 36px;
    height: 36px;
    border: 3px solid #E8DDD4;
    border-top-color: var(--primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  /* Empty */
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-md);
    padding: var(--space-2xl) var(--space-lg);
    text-align: center;
  }
  .empty-title {
    font-size: var(--font-size-xl);
    font-weight: 700;
    color: var(--text);
  }
  .empty-desc {
    font-size: var(--font-size-base);
    color: var(--text-muted);
  }

  /* Sessions list */
  .sessions-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
  }
  .session-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
    text-align: left;
    cursor: pointer;
    position: relative;
    transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s;
    width: 100%;
  }
  .session-card:hover {
    transform: translateY(-4px) scale(1.02);
    box-shadow: 0 16px 32px rgba(163, 150, 138, 0.15);
  }
  .session-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .session-num {
    font-size: var(--font-size-lg);
    font-weight: 700;
    color: var(--text);
  }
  .session-meta {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--font-size-sm);
    color: var(--text-muted);
  }
  .session-type {
    padding: 2px 8px;
    background: #F0E8E0;
    border-radius: var(--radius-pill);
    font-size: 12px;
  }
  .session-arrow {
    position: absolute;
    right: var(--space-lg);
    top: 50%;
    transform: translateY(-50%);
  }

  /* Badges */
  .badge-success {
    background: #E0F5F3;
    color: var(--secondary-dark);
  }
  .badge-error {
    background: #FFE0E0;
    color: var(--error);
  }
  .badge-warning {
    background: #FFF0D0;
    color: var(--tertiary-dark);
  }
  .badge-default {
    background: #F0E8E0;
    color: var(--text-muted);
  }
</style>
