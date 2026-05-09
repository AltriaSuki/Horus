<script>
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { invoke } from '@tauri-apps/api/core';
  import { currentReport, totalTrialsTarget } from '$lib/stores/session.js';

  let report = $state(null);
  let loading = $state(true);
  let error = $state(null);
  let totalTrialsValue = $state(160);

  /** Feature friendly-name mapping */
  const featureNames = {
    cv_rt: '反应时稳定性',
    accuracy: '正确率',
    pupil_slope: '瞳孔上升速率',
    mean_rt: '平均反应时间',
    std_rt: '反应时间标准差',
    rt_skewness: '反应时偏斜度',
    omission_rate: '漏答率',
    rt_diff_correct_incorrect: '正误反应时差',
    pupil_max_peak: '瞳孔最大峰值',
    pupil_mean_change: '瞳孔平均变化',
    pupil_std_of_means: '瞳孔均值标准差',
    pupil_overall_var: '瞳孔总体方差',
    pupil_peak_latency: '瞳孔峰值延迟',
    pupil_auc: '瞳孔面积',
    pupil_late_minus_early: '瞳孔后期减前期',
    pupil_slope_var: '瞳孔速率方差',
    pupil_load_diff: '瞳孔负荷差异',
    pupil_peak_load_diff: '瞳孔峰值负荷差异',
    gaze_var_x: '水平注视方差',
    gaze_var_y: '垂直注视方差',
    gaze_path_normalized: '注视路径标准化',
    mean_rt_load1: '低负荷反应时间',
    mean_rt_load2: '高负荷反应时间',
    rt_load_diff: '负荷反应时差',
    acc_load_diff: '负荷正确率差',
    rt_distractor_slope: '干扰物反应时斜率',
    acc_distractor_slope: '干扰物正确率斜率',
  };

  function friendlyName(key) {
    return featureNames[key] || key;
  }

  onMount(async () => {
    const unsubTrials = totalTrialsTarget.subscribe((v) => {
      totalTrialsValue = v;
    });

    // First try from store (just completed)
    const unsub = currentReport.subscribe((r) => {
      if (r) report = r;
    });
    unsub();

    // If we have a session_id query param, try to load from backend
    if (!report) {
      const sessionId = $page.url.searchParams.get('session_id');
      if (sessionId) {
        try {
          const r = await invoke('get_report', { sessionId });
          if (r) report = r;
          else error = '未找到报告';
        } catch (e) {
          error = '加载报告失败: ' + e;
        }
      } else {
        error = '未找到报告数据';
      }
    }
    loading = false;
    unsubTrials();
  });

  // Risk color
  function riskColor(level) {
    if (!level) return 'var(--secondary)';
    const l = level.toLowerCase();
    if (l === 'low' || l === '低风险') return 'var(--secondary)';
    if (l === 'medium' || l === 'moderate' || l === '中风险' || l === '中等关注') return 'var(--tertiary-dark)';
    if (l === 'high' || l === '高风险') return 'var(--error)';
    if (l === 'minimal' || l === '极低关注' || l === '极低') return 'var(--secondary)';
    return 'var(--text-muted)';
  }

  function riskLabel(level) {
    if (!level) return '未知';
    const l = level.toLowerCase();
    // Show Chinese label before the English level (as requested)
    if (l === 'high' || l === '高风险' || l === '高关注') return '高关注 HIGH';
    if (l === 'moderate' || l === 'medium' || l === '中风险' || l === '中等关注') return '中等关注 MODERATE';
    if (l === 'low' || l === '低风险' || l === '较低关注') return '较低关注 LOW';
    if (l === 'minimal' || l === 'min' || l === '极低关注' || l === '极低') return '极低关注 MINIMAL';
    return level;
  }

  // Probability ring
  const ringSize = 180;
  const strokeWidth = 14;
  const radius = (ringSize - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  let probability = $derived(report ? (report.adhd_probability || 0) : 0);
  let strokeDashoffset = $derived(circumference * (1 - probability));

  // Feature importance (sorted)
  let featureImportance = $derived(() => {
    if (!report || !report.feature_importance) return [];
    return Object.entries(report.feature_importance)
      .map(([key, value]) => ({ key, name: friendlyName(key), value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 10);
  });

  let maxImportance = $derived(() => {
    const fi = featureImportance();
    if (fi.length === 0) return 1;
    return Math.max(...fi.map((f) => f.value), 0.001);
  });
</script>

<div class="page-content">
  <a href="/screening" class="back-link">
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M13 4l-6 6 6 6" />
    </svg>
    返回筛查
  </a>

  {#if loading}
    <div class="loading-state">
      <div class="spinner"></div>
      <p>加载报告中...</p>
    </div>
  {:else if error}
    <div class="error-state card">
      <p>{error}</p>
      <button class="btn-primary" onclick={() => goto('/screening')}>返回</button>
    </div>
  {:else if report}
    <div class="result-content animate-fade-in">
      <!-- Probability ring -->
      <div class="probability-section">
        <div class="probability-ring-wrapper">
          <svg width={ringSize} height={ringSize}>
            <circle
              cx={ringSize / 2}
              cy={ringSize / 2}
              r={radius}
              fill="none"
              stroke="#E8DDD4"
              stroke-width={strokeWidth}
            />
            <circle
              cx={ringSize / 2}
              cy={ringSize / 2}
              r={radius}
              fill="none"
              stroke={riskColor(report.risk_level)}
              stroke-width={strokeWidth}
              stroke-linecap="round"
              stroke-dasharray={circumference}
              stroke-dashoffset={strokeDashoffset}
              transform="rotate(-90 {ringSize / 2} {ringSize / 2})"
              style="transition: stroke-dashoffset 1s ease-out;"
            />
          </svg>
          <div class="ring-center">
            <span class="ring-value">{Math.round(probability * 100)}</span>
            <span class="ring-unit">%</span>
          </div>
        </div>
        <div class="risk-badge" style="background: {riskColor(report.risk_level)}; color: white;">
          {riskLabel(report.risk_level)}
        </div>
        <p class="probability-label">ADHD 风险概率</p>
      </div>

      <!-- Child congratulation card -->
      <div class="congrats-card card">
        <h3 class="card-title child-title">给孩子的话</h3>
        <div class="congrats-content">
          <svg class="congrats-icon" width="48" height="48" viewBox="0 0 48 48" fill="none">
            <circle cx="24" cy="24" r="22" fill="var(--tertiary)" opacity="0.3" />
            <circle cx="24" cy="24" r="14" fill="var(--tertiary)" />
            <path d="M17 24l4 4 10-10" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div class="congrats-text">
            <p class="congrats-main">你完成了所有的挑战!</p>
            <p class="congrats-sub">你的专注力表现非常棒，继续保持!</p>
          </div>
        </div>
      </div>

      <!-- Parent explanation card -->
      <div class="parent-card card">
        <h3 class="card-title parent-title">给家长的说明</h3>
        <div class="parent-content">
          <p>本筛查基于 Sternberg 视觉工作记忆范式，通过分析孩子在 {totalTrialsValue} 个试次中的反应时间、正确率、瞳孔变化和注视行为等 27 项指标，利用随机森林模型进行综合评估。</p>
          {#if report.prediction !== undefined}
            <div class="prediction-row">
              <span class="pred-label">模型预测:</span>
              <span class="pred-value" style="color: {riskColor(report.risk_level)}">
                {report.prediction === 1 ? '存在注意力风险' : '注意力表现正常'}
              </span>
            </div>
          {/if}
          <p class="disclaimer">注意: 本结果仅供参考，不构成医学诊断。如有疑虑，请咨询专业医疗机构。</p>
        </div>
      </div>

      <!-- Feature importance -->
      {#if featureImportance().length > 0}
        <div class="features-section">
          <h3 class="section-title">关键指标分析</h3>
          <div class="features-list">
            {#each featureImportance() as feat, i}
              <div class="feature-row">
                <div class="feature-header">
                  <span class="feature-name">{feat.name}</span>
                  <span class="feature-value">{(feat.value * 100).toFixed(1)}%</span>
                </div>
                <div class="feature-bar-bg">
                  <div
                    class="feature-bar-fill"
                    style="width: {(feat.value / maxImportance()) * 100}%; background: {i < 3 ? 'var(--primary)' : 'var(--secondary)'};"
                  ></div>
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .back-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text-muted);
    font-size: var(--font-size-base);
    font-weight: 500;
    margin-bottom: var(--space-md);
  }
  .back-link:hover {
    color: var(--primary);
  }

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
  .error-state {
    text-align: center;
    padding: var(--space-2xl);
    color: var(--error);
  }

  .result-content {
    display: flex;
    flex-direction: column;
    gap: var(--space-xl);
  }

  /* Probability ring */
  .probability-section {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-md);
    padding: var(--space-lg) 0;
  }
  .probability-ring-wrapper {
    position: relative;
  }
  .ring-center {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .ring-value {
    font-size: var(--font-size-3xl);
    font-weight: 800;
    color: var(--text);
  }
  .ring-unit {
    font-size: var(--font-size-lg);
    font-weight: 600;
    color: var(--text-muted);
    margin-top: 8px;
  }
  .risk-badge {
    padding: 6px 20px;
    border-radius: var(--radius-pill);
    font-size: var(--font-size-base);
    font-weight: 700;
  }
  .probability-label {
    font-size: var(--font-size-base);
    color: var(--text-muted);
  }

  /* Cards */
  .card-title {
    font-size: var(--font-size-lg);
    font-weight: 700;
    margin-bottom: var(--space-md);
    padding-bottom: var(--space-sm);
    border-bottom: 2px solid #F0E8E0;
  }
  .child-title {
    color: var(--tertiary-dark);
  }
  .parent-title {
    color: var(--primary);
  }

  /* Congrats */
  .congrats-content {
    display: flex;
    align-items: center;
    gap: var(--space-md);
  }
  .congrats-icon {
    flex-shrink: 0;
  }
  .congrats-main {
    font-size: var(--font-size-lg);
    font-weight: 700;
    color: var(--text);
  }
  .congrats-sub {
    font-size: var(--font-size-base);
    color: var(--text-muted);
    margin-top: 4px;
  }

  /* Parent */
  .parent-content {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
    font-size: var(--font-size-base);
    color: var(--text);
    line-height: 1.7;
  }
  .prediction-row {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: var(--space-sm) var(--space-md);
    background: #FFF0E0;
    border-radius: var(--radius-sm);
  }
  .pred-label {
    font-weight: 600;
  }
  .pred-value {
    font-weight: 700;
  }
  .disclaimer {
    font-size: var(--font-size-sm);
    color: var(--text-muted);
    padding: var(--space-sm) var(--space-md);
    background: #F5F0EB;
    border-radius: var(--radius-sm);
    border-left: 3px solid var(--text-muted);
  }

  /* Feature importance */
  .features-section {
    background: var(--surface);
    border-radius: var(--radius-md);
    padding: var(--space-lg);
    box-shadow: var(--shadow-sm);
  }
  .features-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
  }
  .feature-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .feature-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .feature-name {
    font-size: var(--font-size-sm);
    font-weight: 600;
    color: var(--text);
  }
  .feature-value {
    font-size: var(--font-size-sm);
    color: var(--text-muted);
    font-weight: 500;
  }
  .feature-bar-bg {
    height: 8px;
    background: #F0E8E0;
    border-radius: 4px;
    overflow: hidden;
  }
  .feature-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.8s ease-out;
    min-width: 2px;
  }
</style>
