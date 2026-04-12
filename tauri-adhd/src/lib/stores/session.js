/**
 * Session store — manages screening state across the app.
 *
 * Uses Svelte 5 runes-compatible writable stores (classic Svelte store API).
 * Pages subscribe via $sessionStore, etc.
 */
import { writable, derived } from 'svelte/store';

/** Phase of the current screening workflow */
export const PHASES = {
  IDLE: 'idle',
  CALIBRATING: 'calibrating',
  RUNNING: 'running',
  BREAK: 'break',
  GENERATING_REPORT: 'generating_report',
  DONE: 'done',
  ERROR: 'error',
};

/** Current session row from Rust */
export const currentSession = writable(null);

/** Current subject for the session */
export const currentSubject = writable(null);

/** Phase of the screening workflow */
export const phase = writable(PHASES.IDLE);

/** Calibration state */
export const calibration = writable({
  pointsDone: 0,
  totalPoints: 13,
  meanError: null,
  validationDone: false,
});

/** Trial results collected during the Sternberg task */
export const trialResults = writable([]);

/** Current block number (1-8) */
export const currentBlock = writable(1);

/** Current trial within block (1-20) */
export const currentTrialInBlock = writable(1);

/** Total trials completed */
export const totalTrialsCompleted = writable(0);

/** The final ADHD report, if available */
export const currentReport = writable(null);

/** Error message, if any */
export const sessionError = writable(null);

/* ── Derived stores ────────────────────────────────────────────── */

/** Total trial count target */
export const TOTAL_TRIALS = 160;
export const TRIALS_PER_BLOCK = 20;
export const TOTAL_BLOCKS = 8;

/** Overall progress as a fraction 0..1 */
export const progress = derived(totalTrialsCompleted, ($total) => $total / TOTAL_TRIALS);

/** Accuracy so far */
export const accuracy = derived(trialResults, ($trials) => {
  if ($trials.length === 0) return 0;
  const correct = $trials.filter((t) => t.correct).length;
  return correct / $trials.length;
});

/* ── Actions ───────────────────────────────────────────────────── */

/** Reset everything for a new session */
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
  currentBlock.set(1);
  currentTrialInBlock.set(1);
  totalTrialsCompleted.set(0);
  currentReport.set(null);
  sessionError.set(null);
}

/** Record a completed trial */
export function recordTrial(trialResult) {
  trialResults.update((arr) => [...arr, trialResult]);
  totalTrialsCompleted.update((n) => n + 1);

  // Advance block/trial counters
  const newTotal = trialResult.trial_num;
  const block = Math.floor((newTotal - 1) / TRIALS_PER_BLOCK) + 1;
  const trialInBlock = ((newTotal - 1) % TRIALS_PER_BLOCK) + 1;
  currentBlock.set(block);
  currentTrialInBlock.set(trialInBlock);
}

/** Update calibration progress */
export function recordCalibrationPoint() {
  calibration.update((cal) => ({
    ...cal,
    pointsDone: cal.pointsDone + 1,
  }));
}
