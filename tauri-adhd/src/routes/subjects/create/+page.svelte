<script>
  import { invoke } from '@tauri-apps/api/core';
  import { goto } from '$app/navigation';

  let id = $state('');
  let displayName = $state('');
  let sex = $state('male');
  let submitting = $state(false);
  let error = $state(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!id.trim() || !displayName.trim()) return;

    submitting = true;
    error = null;
    try {
      await invoke('create_subject', {
        id: id.trim(),
        displayName: displayName.trim(),
        sex: sex,
      });
      goto('/subjects');
    } catch (e) {
      error = '创建失败: ' + e;
      console.error(e);
    } finally {
      submitting = false;
    }
  }

  let canSubmit = $derived(id.trim().length > 0 && displayName.trim().length > 0 && !submitting);
</script>

<div class="page-content">
  <a href="/subjects" class="back-link">
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M13 4l-6 6 6 6" />
    </svg>
    返回
  </a>

  <h1 class="section-title" style="margin-top: var(--space-md);">添加被试</h1>

  <form class="create-form animate-fade-in" onsubmit={handleSubmit}>
    <div class="form-group">
      <label class="form-label" for="subject-id">被试编号</label>
      <input
        id="subject-id"
        class="input-field"
        type="text"
        placeholder="例如: S001"
        bind:value={id}
        required
      />
    </div>

    <div class="form-group">
      <label class="form-label" for="display-name">姓名/昵称</label>
      <input
        id="display-name"
        class="input-field"
        type="text"
        placeholder="例如: 小明"
        bind:value={displayName}
        required
      />
    </div>

    <div class="form-group">
      <label class="form-label">性别</label>
      <div class="sex-toggle">
        <button
          type="button"
          class="sex-tile"
          class:active={sex === 'male'}
          onclick={() => (sex = 'male')}
        >
          <div class="sex-icon male-icon">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="14" cy="18" r="8" />
              <path d="M20 12l6-6" />
              <path d="M22 6h4v4" />
            </svg>
          </div>
          <span>男生</span>
        </button>
        <button
          type="button"
          class="sex-tile"
          class:active={sex === 'female'}
          onclick={() => (sex = 'female')}
        >
          <div class="sex-icon female-icon">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="16" cy="12" r="8" />
              <path d="M16 20v8" />
              <path d="M12 25h8" />
            </svg>
          </div>
          <span>女生</span>
        </button>
      </div>
    </div>

    {#if error}
      <div class="error-msg">{error}</div>
    {/if}

    <button type="submit" class="btn-primary submit-btn" disabled={!canSubmit}>
      {#if submitting}
        提交中...
      {:else}
        创建被试
      {/if}
    </button>
  </form>
</div>

<style>
  .back-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text-muted);
    font-size: var(--font-size-base);
    font-weight: 500;
  }
  .back-link:hover {
    color: var(--primary);
  }

  .create-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-lg);
    margin-top: var(--space-lg);
    max-width: 480px;
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .form-label {
    font-size: var(--font-size-base);
    font-weight: 600;
    color: var(--text);
  }

  /* Sex toggle tiles */
  .sex-toggle {
    display: flex;
    gap: var(--space-md);
  }
  .sex-tile {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-sm);
    padding: var(--space-lg);
    background: var(--surface);
    border: 2px solid #E8DDD4;
    border-radius: var(--radius-md);
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    font-size: var(--font-size-base);
    font-weight: 600;
    color: var(--text-muted);
  }
  .sex-tile.active {
    border-color: var(--primary);
    background: #FFF0E0;
    color: var(--text);
  }
  .sex-tile:hover:not(.active) {
    border-color: var(--primary-light);
  }
  .sex-icon {
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    background: #F0E8E0;
  }
  .sex-tile.active .male-icon {
    background: #E0F0FF;
    color: #4D96FF;
  }
  .sex-tile.active .female-icon {
    background: #FFE0EA;
    color: #E63966;
  }

  .error-msg {
    color: var(--error);
    font-size: var(--font-size-sm);
    padding: var(--space-sm) var(--space-md);
    background: #FFE0E0;
    border-radius: var(--radius-sm);
  }

  .submit-btn {
    width: 100%;
    margin-top: var(--space-md);
  }
</style>
