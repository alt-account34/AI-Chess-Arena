import { esc, fmtTime, fmtTokens } from './view.js';

const $ = s => document.querySelector(s);
const filter = $('#filter'), filterTa = $('#filter-ta');
try {
  filter.value = localStorage.getItem('arena.recordFilter') || 'off';
  filterTa.value = localStorage.getItem('arena.recordFilterTa') || 'all';
} catch { /* storage unavailable */ }
filter.onchange = load;
filterTa.onchange = load;
load();

const wld = r => `${r.w}–${r.l}–${r.d}`;
const num = (v, d = 1) => (v == null ? '–' : Number(v).toFixed(d));

async function load() {
  try {
    localStorage.setItem('arena.recordFilter', filter.value);
    localStorage.setItem('arena.recordFilterTa', filterTa.value);
  } catch { /* ignore */ }
  const data = await fetch(`/api/records?legal_moves=${filter.value}&think_aloud=${filterTa.value}`).then(r => r.json());
  const agents = Object.entries(data.agents).sort((a, b) => b[1].games - a[1].games);
  $('#agents').innerHTML = `<thead><tr>
      <th>Agent</th><th class="n">Games</th><th class="n">W</th><th class="n">L</th><th class="n">D</th>
      <th class="n">Score</th><th class="n">As White (W–L–D)</th><th class="n">As Black (W–L–D)</th>
      <th class="n">Avg think / move</th><th class="n">Avg illegal / game</th><th class="n">Avg length (plies)</th>
      <th class="n" title="average tokens per move (input / output)">Avg tokens / move</th>
      <th class="n" title="all games in this category (input / output)">Total tokens</th>
      <th class="n">Tool violations</th><th class="n">Unfinished</th></tr></thead><tbody>` +
    (agents.length ? agents.map(([id, a]) => {
      const score = a.games ? ((a.total.w + a.total.d / 2) / a.games * 100).toFixed(0) + '%' : '–';
      return `<tr><td><strong>${esc(a.name)}</strong><br><span class="muted">${esc(id)}</span></td>
        <td class="n">${a.games}</td><td class="n">${a.total.w}</td><td class="n">${a.total.l}</td><td class="n">${a.total.d}</td>
        <td class="n">${score}</td><td class="n">${wld(a.as_white)}</td><td class="n">${wld(a.as_black)}</td>
        <td class="n">${a.avg_think_per_move == null ? '–' : fmtTime(a.avg_think_per_move)}</td>
        <td class="n">${num(a.avg_illegal_per_game, 2)}</td><td class="n">${num(a.avg_game_plies)}</td>
        <td class="n">${a.avg_tokens_in_per_move == null ? '–' : fmtTokens(a.avg_tokens_in_per_move) + ' / ' + fmtTokens(a.avg_tokens_out_per_move)}</td>
        <td class="n">${a.token_moves ? fmtTokens(a.tokens_in) + ' / ' + fmtTokens(a.tokens_out) : '–'}</td>
        <td class="n">${a.tool_violations}</td><td class="n">${a.unfinished}</td></tr>`;
    }).join('') : '<tr><td colspan="15" class="muted">No games in this category yet.</td></tr>') + '</tbody>';

  const m = data.matchups.sort((a, b) => b.games - a.games);
  $('#matchups').innerHTML = `<thead><tr><th>White</th><th>Black</th><th class="n">Games</th>
      <th class="n">White wins</th><th class="n">Black wins</th><th class="n">Draws</th><th class="n">Avg length (plies)</th></tr></thead><tbody>` +
    (m.length ? m.map(x => `<tr><td>${esc(x.white_name)}</td><td>${esc(x.black_name)}</td><td class="n">${x.games}</td>
        <td class="n">${x.white_wins}</td><td class="n">${x.black_wins}</td><td class="n">${x.draws}</td>
        <td class="n">${num(x.avg_game_plies)}</td></tr>`).join('')
      : '<tr><td colspan="7" class="muted">No games in this category yet.</td></tr>') + '</tbody>';
}
