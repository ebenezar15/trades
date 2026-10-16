// Indices IndexEdge trades. SENSEX is a BSE index (options on BFO); NIFTY / BANKNIFTY are NSE (NFO).
export const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'SENSEX'] as const;
export const UNDERLYING_OPTIONS = UNDERLYINGS.map((u) => ({ value: u as string, label: u as string }));
