import {
  $, esc, other, agentName, material, frameAt, lastKnownEval, evalText, turnFromFen,
  Board, EvalBar, EvalChart, ResignPanel, renderPlayer, renderMoves, renderEvents, resultBanner, consoleControls,
} from './view.js';

const gameId = new URLSearchParams(location.search).get('id');
if (gameId) showGame(gameId); else showList();

// ---------------------------------------------------------------- list
function showList() {
  $('#list').classList.remove('hidden');
  const filter = $('#filter'), filterTa = $('#filter-ta');
  try {
    filter.value = localStorage.getItem('arena.filter') || 'all';
    filterTa.value = localStorage.getItem('arena.filterTa') || 'all';
  } catch { /* storage unavailable */ }
  const load = async () => {
    try { localStorage.setItem('arena.filter', filter.value); localStorage.setItem('arena.filterTa', filterTa.value); } catch { /* ignore */ }
    const games = await fetch(`/api/games?legal_moves=${filter.value}&think_aloud=${filterTa.value}`).then(r => r.json());
    $('#none').classList.toggle('hidden', games.length > 0);
    $('#games').innerHTML = games.map(g => `
      <tr class="click" data-id="${esc(g.id)}">
        <td>${esc(g.started_at.replace('T', ' '))}</td>
        <td>${esc(g.white_name)}</td><td>${esc(g.black_name)}</td>
        <td><strong>${esc(g.result)}</strong></td>
        <td>${esc(g.reason || g.termination || '')}</td>
        <td class="n">${g.plies}</td>
        <td>${esc(g.opening || 'free')}</td>
        <td>${[g.legal_moves_sent ? 'legal moves' : '', g.think_aloud ? 'think aloud' : ''].filter(Boolean).join(', ') || '–'}</td></tr>`).join('');
    $('#games').querySelectorAll('tr').forEach(tr =>
      tr.addEventListener('click', () => { location.href = `/replay?id=${encodeURIComponent(tr.dataset.id)}`; }));
  };
  filter.onchange = load;
  filterTa.onchange = load;
  load();
}

// ---------------------------------------------------------------- viewer
async function showGame(id) {
  const r = await fetch(`/api/games/${encodeURIComponent(id)}`);
  if (!r.ok) {
    $('#list').classList.remove('hidden');
    $('#list').innerHTML = `<p class="error">Game ${esc(id)} not found.</p><a href="/replay">All games</a>`;
    return;
  }
  const game = await r.json();
  $('#viewer').classList.remove('hidden');
  document.title = `Replay — ${agentName(game.white)} vs ${agentName(game.black)}`;

  const board = new Board($('#board'));
  const evalBar = new EvalBar($('#evalbar'));
  const resignPanel = new ResignPanel();
  const chart = new EvalChart($('#chart'), i => go(i));
  const total = game.plies.length;
  let n = total;
  let timer = null;

  $('#end-text').innerHTML = resultBanner(game);
  $('#banner').classList.toggle('neutral', game.result === '*');
  $('#btn-resign').classList.toggle('hidden', !game.resignation);
  $('#btn-resign').onclick = () => resignPanel.open(game);
  $('#pgn-link').href = `/api/games/${encodeURIComponent(id)}/pgn`;
  const s = game.settings;
  $('#meta').innerHTML = `${esc(game.started_at.replace('T', ' '))} → ${esc((game.finished_at || '').replace('T', ' '))}<br>
    Opening: <strong>${game.opening ? esc(game.opening.name) + (game.opening.eco ? ' (' + esc(game.opening.eco) + ')' : '') : 'free (agents chose)'}</strong><br>
    Legal moves sent: <strong>${s.send_legal_moves ? 'on (assisted)' : 'off'}</strong> ·
    think aloud <strong>${s.think_aloud ? 'on' : 'off'}</strong> · move cap ${s.move_cap_plies} plies<br>
    Sessions: White <code>${esc(game.white.session_history.join(', ') || '–')}</code>,
    Black <code>${esc(game.black.session_history.join(', ') || '–')}</code>`;
  renderEvents($('#events'), game.events);
  consoleControls($('#consoles'), id);

  const engineSeen = [game.start_eval, ...game.plies.map(p => p.eval)].some(Boolean);
  const humans = ['white', 'black'].filter(c => game[c].agent.cli === 'human');
  const bottom = humans.length === 1 ? humans[0] : 'white';
  board.setOrientation(bottom);

  // animate: show the move's classification with the full-square flash (stepping forward)
  function render(animate = false) {
    const f = frameAt(game, n);
    board.set(f.fen, f.lastUci, f.inCheck);
    const rec = n > 0 ? game.plies[n - 1] : null;
    if (rec?.classification && rec.uci) board.showMark(rec.uci.slice(2, 4), rec.classification.label, animate);
    else board.clearMark();
    evalBar.set(lastKnownEval(f.evals, n), engineSeen, bottom === 'black');
    chart.set(f.evals, n);
    $('#eval-now').textContent = f.eval ? `· ${evalText(f.eval)}` : '';
    const mat = material(f.fen);
    const toMove = turnFromFen(f.fen);
    for (const [slot, color] of [['#player-top', other(bottom)], ['#player-bottom', bottom]]) {
      const violations = game.plies.slice(0, n).filter(p => p.color === color)
        .reduce((k, p) => k + p.tool_violations.length, 0);
      renderPlayer($(slot), {
        color, side: game[color], think: f.clocks[color], illegal: f.illegal[color], mat, tokens: f.tokens[color],
        toMove: n < total && toMove === color, thinking: false, violations,
      });
    }
    $('#pos').textContent = n === 0 ? 'Start' : `Ply ${n} / ${total}`;
    renderMoves($('#moves'), game, n, i => go(i));
  }

  function go(i, stepping = false) {
    const prev = n;
    n = Math.max(0, Math.min(total, i));
    render(stepping && n === prev + 1);
    // stepping onto the final position shows the resignation, as in the live view
    if (stepping && n === total && prev !== total && game.resignation) resignPanel.open(game);
  }
  function stop() { clearInterval(timer); timer = null; $('#nav [data-a=play]').textContent = '▶ Play'; }
  function act(a) {
    if (a === 'play') {
      if (timer) return stop();
      if (n >= total) go(0);
      $('#nav [data-a=play]').textContent = '⏸ Pause';
      timer = setInterval(() => { if (n >= total) return stop(); go(n + 1, true); }, 900);
      return;
    }
    stop();
    if (a === 'first') go(0);
    if (a === 'prev') go(n - 1);
    if (a === 'next') go(n + 1, true);
    if (a === 'last') go(total, true);
  }
  $('#nav').querySelectorAll('button[data-a]').forEach(b => b.addEventListener('click', () => act(b.dataset.a)));
  window.addEventListener('keydown', e => {
    if (resignPanel.isOpen || e.target?.matches?.('input, select, textarea')) return;
    const map = { ArrowLeft: 'prev', ArrowRight: 'next', Home: 'first', End: 'last', ' ': 'play' };
    if (map[e.key]) { e.preventDefault(); act(map[e.key]); }
  });
  render();
}
