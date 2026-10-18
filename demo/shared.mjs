export const SPEEDS = [0.5, 1, 2, 4, 8];
export function readSpeed() {
  try { const value = Number(localStorage.getItem('arena.demo.speed')); return SPEEDS.includes(value) ? value : 1; } catch { return 1; }
}
export function saveSpeed(value) { try { localStorage.setItem('arena.demo.speed', String(value)); } catch { /* optional preference */ } }
export async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not load ${url}`);
  return response.json();
}
