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
        <svg width="80" height="80" viewBox="0 0 80 80" fill="none">
          <circle cx="40" cy="40" r="36" fill="#FFF0E0" stroke="var(--primary-light)" stroke-width="2" />
          <rect x="26" y="22" width="28" height="36" rx="4" fill="var(--primary-light)" />
          <line x1="32" y1="32" x2="48" y2="32" stroke="white" stroke-width="2" stroke-linecap="round" />
          <line x1="32" y1="38" x2="44" y2="38" stroke="white" stroke-width="2" stroke-linecap="round" />
          <line x1="32" y1="44" x2="46" y2="44" stroke="white" stroke-width="2" stroke-linecap="round" />
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
    transition: transform 0.15s, box-shadow 0.15s;
    width: 100%;
  }
  .session-card:hover {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
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
