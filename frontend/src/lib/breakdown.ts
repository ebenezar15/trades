import type { Breakdown, BreakdownRow } from '../api/types';

/** Accepts either an array of rows (with `key`) or a mapping key -> row and returns rows. */
export function normalizeBreakdown(b: Breakdown | undefined | null): BreakdownRow[] {
  if (!b) return [];
  if (Array.isArray(b)) return b.map((r, i) => ({ ...r, key: String(r.key ?? r.strategy ?? r.hour ?? r.weekday ?? r.regime ?? i) }));
  return Object.entries(b).map(([key, r]) => ({ ...(r as BreakdownRow), key }));
}
