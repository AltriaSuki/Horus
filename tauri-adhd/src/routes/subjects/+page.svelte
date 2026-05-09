<script>
  import { invoke } from '@tauri-apps/api/core';
  import { onMount } from 'svelte';

  let subjects = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let deletingId = $state('');

  const avatarColors = [
    '#FF8C42', '#4ECDC4', '#FFD166', '#E63946',
    '#A06CD5', '#6BCB77', '#FF6B6B', '#4D96FF',
  ];

  function getAvatarColor(index) {
    return avatarColors[index % avatarColors.length];
  }

  function getInitial(name) {
    return name ? name.charAt(0) : '?';
  }

  onMount(async () => {
    await loadSubjects();
  });

  async function loadSubjects() {
    loading = true;
    error = null;
    try {
      subjects = await invoke('list_subjects');
    } catch (e) {
      error = '加载被试列表失败: ' + e;
      console.error(e);
    } finally {
      loading = false;
    }
  }

  async function deleteSubject(subject, e) {
    e.stopPropagation();
    if (deletingId) return;

    const ok = confirm(`确认删除被试 ${subject.display_name}（${subject.id}）？\n将同时删除该被试的所有历史记录。`);
    if (!ok) return;

    deletingId = subject.id;
    error = null;
    try {
      await invoke('delete_subject', { subjectId: subject.id });
      await loadSubjects();
    } catch (err) {
      error = '删除被试失败: ' + err;
      console.error(err);
    } finally {
      deletingId = '';
    }
  }
</script>

<div class="page-content">
  <div class="page-header">
    <h1 class="section-title">被试管理</h1>
    <a href="/subjects/create" class="btn-primary add-btn">
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <line x1="10" y1="4" x2="10" y2="16" />
        <line x1="4" y1="10" x2="16" y2="10" />
      </svg>
      添加被试
    </a>
  </div>

  {#if loading}
    <div class="loading-state">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>
  {:else if error}
    <div class="error-state card">
      <p>{error}</p>
      <button class="btn-secondary" onclick={loadSubjects}>重试</button>
    </div>
  {:else if subjects.length === 0}
    <div class="empty-state animate-fade-in">
      <div class="empty-illustration">
        <svg width="120" height="120" viewBox="0 0 120 120" fill="none" class="animate-float">
          <circle cx="60" cy="60" r="50" fill="rgba(255,255,255,0.4)" />
          <!-- Head -->
          <circle cx="60" cy="45" r="18" fill="var(--primary)"/>
          <path d="M44 45 Q 60 20 76 45" fill="var(--primary-dark)"/>
          <!-- Face details -->
          <circle cx="53" cy="48" r="3" fill="white"/>
          <circle cx="67" cy="48" r="3" fill="white"/>
          <path d="M56 54 Q 60 58 64 54" stroke="white" stroke-width="3" stroke-linecap="round" fill="none"/>
          <!-- Body -->
          <path d="M35 85 C35 70 45 62 60 62 C75 62 85 70 85 85" fill="var(--primary)"/>
        </svg>
      </div>
      <h2 class="empty-title">还没有被试</h2>
      <p class="empty-desc">点击上方按钮添加第一个被试</p>
    </div>
  {:else}
    <div class="subject-grid">
      {#each subjects as subject, i}
        <div class="subject-card card animate-fade-in stagger-{(i % 5) + 1}">
          <button
            class="btn-delete-subject"
            onclick={(e) => deleteSubject(subject, e)}
            disabled={!!deletingId}
            title="删除被试"
          >
            {deletingId === subject.id ? '删除中...' : '删除'}
          </button>
          <div class="avatar" style="background: {getAvatarColor(i)}">
            <span class="avatar-letter">{getInitial(subject.display_name)}</span>
          </div>
          <div class="subject-info">
            <span class="subject-name">{subject.display_name}</span>
            <span class="subject-id">{subject.id}</span>
          </div>
          {#if subject.sex}
            <span class="sex-badge" class:male={subject.sex === 'male'} class:female={subject.sex === 'female'}>
              {subject.sex === 'male' ? '男' : '女'}
            </span>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-lg);
  }
  .add-btn {
    font-size: var(--font-size-base);
    padding: 10px 20px;
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

  /* Error */
  .error-state {
    text-align: center;
    padding: var(--space-2xl);
    color: var(--error);
  }
  .error-state .btn-secondary {
    margin-top: var(--space-md);
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
  .empty-illustration {
    margin-bottom: var(--space-md);
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

  /* Subject grid */
  .subject-grid {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
  }
  .subject-card {
    position: relative;
    display: flex;
    align-items: center;
    gap: var(--space-md);
    padding: var(--space-md) var(--space-lg);
    transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s;
  }
  .subject-card:hover {
    transform: translateY(-4px) scale(1.02);
    box-shadow: 0 16px 32px rgba(163, 150, 138, 0.15);
  }
  .avatar {
    width: 56px;
    height: 56px;
    border-radius: 40%; /* Squircle */
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    box-shadow: inset 0 -4px 8px rgba(0,0,0,0.1), 0 4px 12px rgba(0,0,0,0.15);
  }
  .avatar-letter {
    font-size: var(--font-size-xl);
    font-weight: 700;
    color: white;
  }
  .subject-info {
    display: flex;
    flex-direction: column;
    flex: 1;
  }
  .subject-name {
    font-size: var(--font-size-lg);
    font-weight: 600;
    color: var(--text);
  }
  .subject-id {
    font-size: var(--font-size-sm);
    color: var(--text-muted);
  }
  .sex-badge {
    padding: 4px 12px;
    border-radius: var(--radius-pill);
    font-size: var(--font-size-sm);
    font-weight: 600;
  }
  .sex-badge.male {
    background: #E0F0FF;
    color: #4D96FF;
  }
  .sex-badge.female {
    background: #FFE0EA;
    color: #E63966;
  }

  .btn-delete-subject {
    position: absolute;
    top: 10px;
    right: 10px;
    border: none;
    border-radius: 999px;
    padding: 6px 12px;
    font-size: var(--font-size-xs);
    font-weight: 700;
    color: #fff;
    background: #d95d39;
    cursor: pointer;
    transition: transform 0.2s ease, opacity 0.2s ease;
  }

  .btn-delete-subject:hover:enabled {
    transform: translateY(-1px);
  }

  .btn-delete-subject:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
</style>
