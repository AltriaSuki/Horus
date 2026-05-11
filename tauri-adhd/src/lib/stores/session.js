import { writable, derived } from 'svelte/store';
import { get } from 'svelte/store';

export const PHASES = {
  IDLE: 'idle',
  CALIBRATING: 'calibrating',
  RUNNING: 'running',
  BREAK: 'break',
  GENERATING_REPORT: 'generating_report',
  DONE: 'done',
  ERROR: 'error',
};

export const currentSession = writable(null);

export const currentSubject = writable(null);

export const phase = writable(PHASES.IDLE);

export const calibration = writable({
  pointsDone: 0,
  totalPoints: 13,
  meanError: null,
  validationDone: false,
});

export const trialResults = writable([]);

export const taskConfig = writable({
  totalBlocks: 8,
  trialsPerBlock: 20,
});

export const currentBlock = writable(1);

export const currentTrialInBlock = writable(1);

export const totalTrialsCompleted = writable(0);

export const currentReport = writable(null);

export const sessionError = writable(null);

export const TOTAL_TRIALS = 160;
export const TRIALS_PER_BLOCK = 20;
export const TOTAL_BLOCKS = 8;

export const totalTrialsTarget = derived(taskConfig, ($cfg) => $cfg.totalBlocks * $cfg.trialsPerBlock);

export const progress = derived([totalTrialsCompleted, totalTrialsTarget], ([$total, $target]) => {
  if (!$target || $target <= 0) return 0;
  return Math.min(1, $total / $target);
});

export const accuracy = derived(trialResults, ($trials) => {
  if ($trials.length === 0) return 0;
  const correct = $trials.filter((t) => t.correct).length;
  return correct / $trials.length;
});

export function resetSession() {
  currentSession.set(null);
  currentSubject.set(null);
  phase.set(PHASES.IDLE);
  calibration.set({
    pointsDone: 0,
    totalPoints: 13,
    meanError: null,
    validationDone: false,
  });
  trialResults.set([]);
  taskConfig.set({
    totalBlocks: TOTAL_BLOCKS,
    trialsPerBlock: TRIALS_PER_BLOCK,
  });
  currentBlock.set(1);
  currentTrialInBlock.set(1);
  totalTrialsCompleted.set(0);
  currentReport.set(null);
  sessionError.set(null);
}

// Used by short local test runs.
export function setTaskConfig(config) {
  const totalBlocks = Math.max(1, Number(config?.totalBlocks ?? TOTAL_BLOCKS) || TOTAL_BLOCKS);
  const trialsPerBlock = Math.max(1, Number(config?.trialsPerBlock ?? TRIALS_PER_BLOCK) || TRIALS_PER_BLOCK);
  taskConfig.set({ totalBlocks, trialsPerBlock });
}

export function recordTrial(trialResult) {
  trialResults.update((arr) => [...arr, trialResult]);
  totalTrialsCompleted.update((n) => n + 1);

  const cfg = get(taskConfig);
  const perBlock = Math.max(1, cfg.trialsPerBlock || TRIALS_PER_BLOCK);
  const newTotal = trialResult.trial_num;
  const block = Math.floor((newTotal - 1) / perBlock) + 1;
  const trialInBlock = ((newTotal - 1) % perBlock) + 1;
  currentBlock.set(block);
  currentTrialInBlock.set(trialInBlock);
}

export function recordCalibrationPoint() {
  calibration.update((cal) => ({
    ...cal,
    pointsDone: cal.pointsDone + 1,
  }));
}
