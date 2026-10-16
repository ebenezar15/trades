// Short alert beeps via WebAudio. iOS Safari only lets audio start inside a user gesture, so the AudioContext is
// created/resumed on the first tap anywhere (or the explicit "Enable sound alerts" button) and reused afterwards.
import { useSyncExternalStore } from 'react';

type Ctor = typeof AudioContext;
let ctx: AudioContext | null = null;
let unlocked = false;
const listeners = new Set<() => void>();
const KEY = 'ie-sound';

function wanted(): boolean {
  try {
    return localStorage.getItem(KEY) !== '0';
  } catch {
    return true;
  }
}
let enabled = wanted();

function notify() {
  listeners.forEach((l) => l());
}

/** Create/resume the audio context. Must be called from a user gesture on iOS. */
export function unlockAudio(): boolean {
  try {
    const AC: Ctor | undefined = window.AudioContext ?? (window as unknown as { webkitAudioContext?: Ctor }).webkitAudioContext;
    if (!AC) return false;
    ctx ??= new AC();
    if (ctx.state === 'suspended') void ctx.resume();
    // play one silent sample — this is what actually unlocks output on older iOS
    const buf = ctx.createBuffer(1, 1, 22050);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(0);
    if (!unlocked) {
      unlocked = true;
      notify();
    }
    return true;
  } catch {
    return false;
  }
}

/** Explicit opt-in from the 🔔 button: unlock + remember the choice + confirmation chirp. */
export function enableSound() {
  enabled = true;
  try {
    localStorage.setItem(KEY, '1');
  } catch {
    /* ignore */
  }
  unlockAudio();
  notify();
  beep('info');
}

export function disableSound() {
  enabled = false;
  try {
    localStorage.setItem(KEY, '0');
  } catch {
    /* ignore */
  }
  notify();
}

/** Short tone pattern: stop = three low urgent pulses, target = rising two-tone, info = single chirp. */
export function beep(kind: 'stop' | 'target' | 'info' = 'info') {
  if (!enabled || !ctx || ctx.state === 'closed') return;
  try {
    if (ctx.state === 'suspended') void ctx.resume();
    const pattern: Array<[number, number, number]> =
      kind === 'stop' ? [[440, 0, 0.16], [440, 0.22, 0.16], [440, 0.44, 0.24]] : kind === 'target' ? [[660, 0, 0.14], [990, 0.17, 0.22]] : [[880, 0, 0.1]];
    const t0 = ctx.currentTime + 0.01;
    for (const [freq, at, dur] of pattern) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = kind === 'stop' ? 'square' : 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t0 + at);
      gain.gain.exponentialRampToValueAtTime(kind === 'stop' ? 0.18 : 0.3, t0 + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0 + at);
      osc.stop(t0 + at + dur + 0.02);
    }
  } catch {
    /* audio is best-effort */
  }
}

/** Classify an alert text into a beep kind / banner tone. */
export function alertKind(text: string | null | undefined): 'stop' | 'target' | 'info' {
  if (!text) return 'info';
  if (/stop|sl hit|exit now/i.test(text)) return 'stop';
  if (/target|profit/i.test(text)) return 'target';
  return 'info';
}

// unlock silently on the first tap anywhere (only takes effect if the user hasn't turned sound off)
if (typeof window !== 'undefined') {
  const first = () => {
    if (enabled) unlockAudio();
    if (unlocked) {
      window.removeEventListener('pointerdown', first, true);
      window.removeEventListener('keydown', first, true);
    }
  };
  window.addEventListener('pointerdown', first, true);
  window.addEventListener('keydown', first, true);
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};
let snap = '';
const getSnap = () => {
  const s = `${enabled ? 1 : 0}${unlocked ? 1 : 0}`;
  if (s !== snap) snap = s;
  return snap;
};

/** { enabled: user wants sound, ready: audio unlocked by a gesture } */
export function useSound(): { enabled: boolean; ready: boolean } {
  const s = useSyncExternalStore(subscribe, getSnap);
  return { enabled: s[0] === '1', ready: s[1] === '1' };
}
