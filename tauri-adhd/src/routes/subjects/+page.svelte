<script>
  import { invoke } from '@tauri-apps/api/core';
  import { onMount } from 'svelte';

  let subjects = $state([]);
  let loading = $state(true);
  let error = $state(null);

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
        <svg width="120" height="120" viewBox="0 0 120 120" fill="none">
          <circle cx="60" cy="60" r="56" fill="#FFF0E0" stroke="var(--primary-light)" stroke-width="2" />
          <circle cx="60" cy="42" r="16" fill="var(--primary-light)" />
          <path d="M35 85c0-13.8 11.2-25 25-25s25 11.2 25 25" fill="var(--primary-light)" />
          <circle cx="60" cy="42" r="12" fill="var(--tertiary)" />
          <circle cx="55" cy="40" r="2" fill="var(--text)" />
          <circle cx="65" cy="40" r="2" fill="var(--text)" />
          <path d="M56 47c0 0 2 3 4 3s4-3 4-3" stroke="var(--text)" stroke-width="1.5" stroke-linecap="round" fill="none" />
        </svg>
      </div>
      <h2 class="empty-title">还没有被试</h2>
      <p class="empty-desc">点击上方按钮添加第一个被试</p>
    </div>
  {:else}
    <div class="subject-grid animate-fade-in">
      {#each subjects as subject, i}
        <div class="subject-card card">
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
    display: flex;
    align-items: center;
    gap: var(--space-md);
    padding: var(--space-md) var(--space-lg);
  }
  .avatar {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
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
</style>
