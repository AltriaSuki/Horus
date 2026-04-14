<script>
  import { invoke } from '@tauri-apps/api/core';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import '../app.css';

  let { children } = $props();

  let health = $state(null);
  let healthError = $state(false);

  onMount(async () => {
    try {
      health = await invoke('get_health');
    } catch (e) {
      healthError = true;
      console.error('Health check failed:', e);
    }
  });

  const tabs = [
    { path: '/subjects', label: '被试', icon: 'child' },
    { path: '/screening', label: '早筛', icon: 'rocket' },
    { path: '/training', label: '训练', icon: 'gamepad' },
    { path: '/reports', label: '报告', icon: 'chart' },
  ];

  let currentPath = $derived($page.url.pathname);

  function isActive(tabPath) {
    return currentPath.startsWith(tabPath);
  }

  /** Check if we are in a fullscreen task (calibration or running screening) */
  let isFullscreen = $derived(
    currentPath.startsWith('/screening/running')
  );
</script>

{#if isFullscreen}
  {@render children()}
{:else}
  <div class="app-shell">
    <header class="top-bar">
      <div class="top-bar-left">
        <div class="app-logo">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <circle cx="14" cy="14" r="14" fill="var(--primary)" />
            <circle cx="14" cy="14" r="8" fill="white" opacity="0.3" />
            <circle cx="14" cy="14" r="4" fill="white" />
          </svg>
        </div>
        <span class="app-title">ADHD-Screener</span>
      </div>
      <div class="health-chip" class:healthy={health && !healthError} class:error={healthError}>
        <span class="health-dot"></span>
        {#if health}
          <span class="health-text">{health.version}</span>
        {:else if healthError}
          <span class="health-text">离线</span>
        {:else}
          <span class="health-text">连接中...</span>
        {/if}
      </div>
    </header>

    <!-- Page content -->
    <main class="main-content">
      {@render children()}
    </main>

    <!-- Bottom navigation -->
    <nav class="bottom-nav">
      {#each tabs as tab}
        <a
          href={tab.path}
          class="nav-tab"
          class:active={isActive(tab.path)}
        >
          <div class="nav-icon">
            {#if tab.icon === 'child'}
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="7" r="4" />
                <path d="M5.5 21c0-4.5 2.9-8 6.5-8s6.5 3.5 6.5 8" />
              </svg>
            {:else if tab.icon === 'rocket'}
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 00-2.91-.09z" />
                <path d="M12 15l-3-3a22 22 0 012-3.95A12.88 12.88 0 0122 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 01-4 2z" />
                <path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0" />
                <path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" />
              </svg>
            {:else if tab.icon === 'gamepad'}
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="6" width="20" height="12" rx="6" />
                <line x1="6" y1="12" x2="10" y2="12" />
                <line x1="8" y1="10" x2="8" y2="14" />
                <circle cx="15" cy="11" r="0.5" fill="currentColor" />
                <circle cx="18" cy="11" r="0.5" fill="currentColor" />
              </svg>
            {:else if tab.icon === 'chart'}
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M18 20V10" />
                <path d="M12 20V4" />
                <path d="M6 20v-6" />
              </svg>
            {/if}
          </div>
          <span class="nav-label">{tab.label}</span>
        </a>
      {/each}
    </nav>
  </div>
{/if}

<style>
  .app-shell {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--bg);
  }

  /* ── Top bar ───────────────────────────────────────────────── */
  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px var(--space-lg);
    background: rgba(255, 255, 255, 0.4);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    flex-shrink: 0;
    height: 60px;
    z-index: 10;
  }
  .top-bar-left {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }
  .app-logo {
    display: flex;
  }
  .app-title {
    font-size: var(--font-size-lg);
    font-weight: 800;
    color: var(--text);
  }

  /* Health chip */
  .health-chip {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 16px;
    border-radius: var(--radius-pill);
    background: rgba(255, 255, 255, 0.6);
    box-shadow: inset 0 2px 4px rgba(255,255,255,0.8), 0 2px 8px rgba(163, 150, 138, 0.06);
    font-size: 13px;
    font-weight: 700;
    color: var(--text-muted);
  }
  .health-chip.healthy {
    color: var(--secondary-dark);
  }
  .health-chip.error {
    background: #FFF0F0;
    color: var(--error);
  }
  .health-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
  }
  .health-chip.healthy .health-dot {
    background: var(--secondary);
  }
  .health-chip.error .health-dot {
    background: var(--error);
  }

  /* ── Main content ──────────────────────────────────────────── */
  .main-content {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }

  /* ── Bottom nav (Floating Pill) ───────────────────────────── */
  .bottom-nav {
    display: flex;
    position: fixed;
    bottom: var(--space-lg);
    left: 50%;
    transform: translateX(-50%);
    align-items: stretch;
    justify-content: space-around;
    height: 72px;
    width: max-content;
    gap: 8px;
    padding: 8px;
    background: rgba(255, 255, 255, 0.85);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border-radius: var(--radius-pill);
    box-shadow: 0 12px 32px rgba(163, 150, 138, 0.15), 0 0 0 4px rgba(255, 255, 255, 0.5);
    z-index: 50;
  }
  .nav-tab {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    padding: 4px 20px;
    border-radius: var(--radius-pill);
    color: var(--text-muted);
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    text-decoration: none;
  }
  .nav-tab.active {
    background: white;
    color: var(--primary-dark);
    box-shadow: 0 4px 12px rgba(255, 176, 160, 0.2);
    transform: translateY(-2px);
  }
  .nav-icon {
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .nav-label {
    font-size: 13px;
    font-weight: 800;
  }
</style>
