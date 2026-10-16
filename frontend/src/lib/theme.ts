import { useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

function read(): Theme {
  try {
    return localStorage.getItem('ie-theme') === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

const listeners = new Set<(t: Theme) => void>();

export function setTheme(t: Theme) {
  try {
    localStorage.setItem('ie-theme', t);
  } catch {
    /* ignore */
  }
  document.documentElement.classList.toggle('dark', t === 'dark');
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'dark' ? '#0b1220' : '#f4f6fa');
  listeners.forEach((l) => l(t));
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [t, setT] = useState<Theme>(read);
  useEffect(() => {
    listeners.add(setT);
    return () => {
      listeners.delete(setT);
    };
  }, []);
  return [t, setTheme];
}

/** Resolved chart colours for the current theme (lightweight-charts needs concrete colours). */
export function chartColors(t: Theme) {
  return t === 'dark'
    ? { bg: '#11192a', text: '#8c99af', grid: '#1c2740', border: '#26324a', up: '#22c55e', down: '#ef4444' }
    : { bg: '#ffffff', text: '#647084', grid: '#eef1f6', border: '#dadfe8', up: '#16a34a', down: '#dc2626' };
}
