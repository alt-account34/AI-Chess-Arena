import {
  $, esc, fmtTime, GLYPH, colorName, other, agentName, material, frameAt, lastKnownEval, turnFromFen,
  Board, EvalBar, EvalChart, ResignPanel, renderPlayer, renderMoves, renderEvents, resultBanner, consoleControls,
} from './view.js';

let game = null;          // latest snapshot
let skew = 0;             // server clock - local clock (seconds)
let selected = null;      // ply being looked at (null = follow the latest position)
let shownResignFor = null;
let moveError = '';
const seenMarks = new Set();   // classifications already shown (animate each one once)
let firstRender = true;

const board = new Board($('#board'));
const evalBar = new EvalBar($('#evalbar'));
const chart = new EvalChart($('#chart'), n => select(n));
const resignPanel = new ResignPanel();
consoleControls($('#consoles'));          // follows whatever game is live

// ---------------------------------------------------------------- websocket
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  let ping;
  ws.onopen = () => {
    $('#conn').className = 'conn ok'; $('#conn').textContent = 'live';
    ping = setInterval(() => ws.readyState === 1 && ws.send('ping'), 20000);
  };
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.state) skew = msg.state.server_time - Date.now() / 1000;
    if (msg.state && game && msg.state.id !== game.id) { selected = null; moveError = ''; }
    game = msg.state;
    render();
  };
  ws.onclose = () => {
    clearInterval(ping);
    $('#conn').className = 'conn down'; $('#conn').textContent = 'reconnecting…';
    setTimeout(connect, 1500);
  };
}

// ---------------------------------------------------------------- controls
async function post(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) } : { method: 'POST' });
  if (!r.ok) console.warn(path, r.status, await r.text());
  return r;
}
$('#btn-pause').onclick = () => post('/api/game/pause');
$('#btn-resume').onclick = () => post('/api/game/resume');
$('#btn-retry').onclick = () => post('/api/game/retry');
// two-click confirm (no browser dialogs)
function armed(b, label, armedLabel, action) {
  let disarm = null;
  b.onclick = () => {
    clearTimeout(disarm);
    if (!b.classList.contains('armed')) {
      b.classList.add('armed'); b.textContent = armedLabel;
      disarm = setTimeout(() => { b.classList.remove('armed'); b.textContent = label; }, 4000);
      return;
    }
    b.classList.remove('armed'); b.textContent = label;
    action();
  };
}
for (const id of ['#btn-abort', '#btn-abort2']) armed($(id), 'Abort', 'Click again to abort', () => post('/api/game/abort'));
armed($('#btn-resign'), 'Resign', 'Click again to resign', () => post('/api/game/resign', {}));
$('#btn-show-resign').onclick = () => game && resignPanel.open(game);

async function submitMove(uci) {
  moveError = '';
  const r = await fetch('/api/game/move', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uci }) });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    moveError = d.detail || `move rejected (${r.status})`;
    board.reset();
    render();
  }
}

function select(n) {
  if (!game) return;
  n = Math.max(0, Math.min(game.plies.length, n));
  selected = n === game.plies.length ? null : n;
  render();
}
window.addEventListener('keydown', e => {
  if (!game || resignPanel.isOpen || e.target?.matches?.('input, select, textarea')) return;
  const n = selected ?? game.plies.length;
  const map = { ArrowLeft: n - 1, ArrowRight: n + 1, Home: 0, End: game.plies.length };
  if (e.key in map) { e.preventDefault(); select(map[e.key]); }
});

const humans = g => ['white', 'black'].filter(c => g[c].agent.cli === 'human');

// ---------------------------------------------------------------- render
function render() {
  if (!game) {
    $('#empty').classList.remove('hidden');
    $('#game').classList.add('hidden');
    return;
  }
  $('#empty').classList.add('hidden');
  $('#game').classList.remove('hidden');

  const n = game.plies.length;
  const showN = selected ?? n;
  const f = frameAt(game, showN);
  const turn = game.turn;
  const toMove = game.status === 'finished' ? null : turnFromFen(frameAt(game, n).fen);
  const thinkingColor = turn && game.status === 'running' ? turn.color : null;
  const hs = humans(game);
  const bottom = hs.length === 1 ? hs[0] : 'white';     // a lone human plays from the bottom

  board.setOrientation(bottom);
  board.set(f.fen, f.lastUci, f.inCheck);
  const humanTurn = game.status === 'running' && turn?.human && !turn.submitted && selected == null;
  board.setInteractive(humanTurn ? { color: turn.color, dests: turn.dests, onMove: submitMove } : null);

  // classification mark for the move shown on the board
  const rec = showN > 0 ? game.plies[showN - 1] : null;
  const cls = rec?.classification;
  if (cls && rec.uci) {
    const key = `${game.id}|${showN}|${cls.label}`;
    const animate = !firstRender && selected == null && !seenMarks.has(key);
    seenMarks.add(key);
    board.showMark(rec.uci.slice(2, 4), cls.label, animate);
  } else {
    board.clearMark();
  }
  firstRender = false;

  const ev = lastKnownEval(f.evals, showN);
  evalBar.set(ev, game.engine?.available === false ? false : game.engine_available, bottom === 'black');
  chart.set(frameAt(game, n).evals, showN);
  $('#engine-name').textContent = game.engine?.available ? `· ${game.engine.name || 'Stockfish'}`
    : game.engine?.error ? `· unavailable (${game.engine.error})` : '';

  const mat = material(f.fen);
  for (const [slot, color] of [['#player-top', other(bottom)], ['#player-bottom', bottom]]) {
    const side = game[color];
    renderPlayer($(slot), {
      color, side, think: side.think_total, mat, toMove: toMove === color, thinking: thinkingColor === color,
      illegal: { total: side.illegal_total, streak: side.illegal_streak }, violations: side.tool_violations,
      tokens: side.tokens,
    });
  }

  // turn indicator
  const icon = $('#turn-icon');
  const tc = toMove || (game.winner ?? 'white');
  icon.className = `piece-icon ${tc}`; icon.textContent = GLYPH[tc];
  let big = '', sub = '';
  if (game.status === 'finished') {
    big = game.result === '*' ? 'Game over' : `Game over · ${game.result}`;
    sub = esc(game.reason || '');
  } else if (game.status === 'paused') {
    big = 'Paused'; sub = esc(`${colorName(toMove)} (${agentName(game[toMove])}) to move when resumed`);
  } else if (game.status === 'unresponsive') {
    big = `${colorName(toMove)} unresponsive`; sub = esc(game.unresponsive_reason || '');
  } else if (turn?.human) {
    big = `${colorName(turn.color)} to move`;
    sub = turn.submitted ? 'Move sent…'
      : selected != null ? 'Your move: press End (or click the last move) to return to the game'
      : 'Your move: drag a piece, or click it and then a highlighted square';
    if (moveError) sub += ` <span class="error">${esc(moveError)}</span>`;
  } else if (turn) {
    big = `${colorName(turn.color)} to move`;
    sub = `<span class="thinking-dots">${esc(agentName(game[turn.color]))} is thinking</span>` +
      (turn.attempt > 1 ? ` · retry (attempt ${turn.attempt})` : '');
  } else {
    big = 'Starting…';
  }
  $('#turn-big').textContent = big;
  $('#turn-sub').innerHTML = sub;
  $('#settings-line').textContent = `opening: ${game.opening ? game.opening.name : 'free'} · ` +
    `legal moves ${game.settings.send_legal_moves ? 'ON' : 'off'} · ` +
    `think aloud ${game.settings.think_aloud ? 'ON' : 'off'} · cap ${game.settings.move_cap_plies} plies`;

  // controls + banners
  const live = game.status !== 'finished';
  $('#btn-pause').classList.toggle('hidden', !live || game.status === 'paused' || game.status === 'unresponsive');
  $('#btn-pause').disabled = game.pause_requested;
  $('#btn-pause').textContent = game.pause_requested ? 'Pausing after this move…' : 'Pause';
  $('#btn-resume').classList.toggle('hidden', !(game.status === 'paused' || game.pause_requested));
  $('#btn-abort').classList.toggle('hidden', !live);
  $('#btn-resign').classList.toggle('hidden', !live || !hs.length);
  $('#banner-unresp').classList.toggle('hidden', game.status !== 'unresponsive');
  $('#unresp-text').textContent = game.unresponsive_reason || '';
  const pausing = game.status === 'paused' || game.pause_requested;
  $('#banner-pause').classList.toggle('hidden', !pausing || !live);
  $('#pause-text').textContent = game.status === 'paused' ? 'Game paused. Press Resume to continue.'
    : 'Pause requested: the game will pause after the current move.';
  $('#banner-end').classList.toggle('hidden', game.status !== 'finished');
  if (game.status === 'finished') {
    $('#banner-end').classList.toggle('neutral', game.result === '*');
    $('#end-text').innerHTML = resultBanner(game);
    $('#end-replay').href = `/replay?id=${encodeURIComponent(game.id)}`;
    $('#btn-show-resign').classList.toggle('hidden', !game.resignation);
    if (game.resignation && shownResignFor !== game.id) {
      shownResignFor = game.id;
      resignPanel.open(game);
    }
  }

  renderMoves($('#moves'), game, showN, select);
  renderEvents($('#events'), game.events);
  tick();
}

function tick() {
  const t = $('#turn-timer');
  if (!game || !game.turn || game.status === 'finished') { t.textContent = ''; return; }
  const turn = game.turn;
  let s = turn.think_before || 0;
  if (game.status === 'running' && turn.attempt_started_at) s += Date.now() / 1000 + skew - turn.attempt_started_at;
  t.textContent = fmtTime(Math.max(0, s));
}
setInterval(tick, 200);

connect();
