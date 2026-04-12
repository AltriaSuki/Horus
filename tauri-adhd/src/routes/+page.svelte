<script>
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';

  onMount(() => {
    goto('/screening', { replaceState: true });
  });
</script>

<div class="redirect-page">
  <p>正在加载...</p>
</div>

<style>
  .redirect-page {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    color: var(--text-muted);
    font-size: var(--font-size-lg);
  }
</style>
