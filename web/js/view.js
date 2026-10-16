// Shared rendering for the live and replay pages. The live snapshot and the
// saved JSON log have the same shape, so one renderer serves both.
import { Chessground } from '/static/vendor/chessground.min.js';

export const $ = (sel, root = document) => root.querySelector(sel);

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmtTime(s) {
  if (s == null || isNaN(s)) return '–';
  if (s < 60) return s.toFixed(1) + 's';
  const m = Math.floor(s / 60), sec = s - m * 60;
  if (m < 60) return `${m}:${sec.toFixed(0).padStart(2, '0')}`;
  const h = Math.floor(m / 60);
  return `${h}:${String(m % 60).padStart(2, '0')}:${sec.toFixed(0).padStart(2, '0')}`;
}

export function fmtTokens(n) {
  if (n == null || isNaN(n)) return '–';
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1000).toFixed(n < 1e4 ? 1 : 0) + 'k';
  return (n / 1e6).toFixed(2) + 'M';
}

// One-line token summary with the full breakdown in a tooltip.
export function tokenSpan(t, label = 'tokens') {
  if (!t || !t.calls && !t.input && !t.output) return `<span class="muted" title="no token data">${label}: –</span>`;
  const tip = `input ${(t.input || 0).toLocaleString()} (of which cached ${(t.cached || 0).toLocaleString()})\n` +
    `output ${(t.output || 0).toLocaleString()} (of which reasoning ${(t.reasoning || 0).toLocaleString()})` +
    (t.calls ? `\n${t.calls} CLI call(s)` : '');
  return `<span title="${esc(tip)}">${label}: ${fmtTokens(t.input)} in · ${fmtTokens(t.output)} out</span>`;
}

export const GLYPH = { white: '♔', black: '♚' };
export const colorName = c => (c === 'white' ? 'White' : 'Black');
export const other = c => (c === 'white' ? 'black' : 'white');
export const agentName = side => side?.agent?.display_name || side?.agent?.id || '?';

// ---------------------------------------------------------------- material
const VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9 };
const START = { p: 8, n: 2, b: 2, r: 2, q: 1 };
const PIECE_GLYPH = {
  white: { q: '♕', r: '♖', b: '♗', n: '♘', p: '♙' },
  black: { q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' },
};

export function material(fen) {
  const counts = { white: { p: 0, n: 0, b: 0, r: 0, q: 0 }, black: { p: 0, n: 0, b: 0, r: 0, q: 0 } };
  for (const ch of fen.split(' ')[0]) {
    const t = ch.toLowerCase();
    if (!(t in VALUE)) continue;
    counts[ch === t ? 'black' : 'white'][t]++;
  }
  const points = c => Object.entries(counts[c]).reduce((s, [t, n]) => s + n * VALUE[t], 0);
  // pieces each side has captured from the other (promotions can make this approximate)
  const captured = c => {
    const opp = other(c), out = [];
    for (const t of ['q', 'r', 'b', 'n', 'p']) {
      for (let i = 0; i < Math.max(0, START[t] - counts[opp][t]); i++) out.push(PIECE_GLYPH[opp][t]);
    }
    return out;
  };
  const diff = points('white') - points('black');
  return { white: { captured: captured('white'), lead: Math.max(0, diff) },
           black: { captured: captured('black'), lead: Math.max(0, -diff) } };
}

export const turnFromFen = fen => (fen.split(' ')[1] === 'b' ? 'black' : 'white');

// ---------------------------------------------------------------- eval
export function evalPawns(ev) {
  if (!ev) return null;
  if ('mate' in ev && ev.mate !== null && ev.mate !== undefined) {
    if (ev.mate === 0) return ev.winner === 'black' ? -10 : 10;
    return ev.mate > 0 ? 10 : -10;
  }
  return Math.max(-10, Math.min(10, ev.cp / 100));
}

export function evalText(ev) {
  if (!ev) return '…';
  if ('mate' in ev && ev.mate !== null && ev.mate !== undefined) {
    if (ev.mate === 0) return ev.winner === 'black' ? '0-1' : '1-0';
    return (ev.mate > 0 ? '' : '-') + 'M' + Math.abs(ev.mate);
  }
  const p = ev.cp / 100;
  return (p > 0 ? '+' : '') + p.toFixed(2);
}

// White's share of the bar, using a win-probability curve like lichess's.
function whiteShare(ev) {
  if (!ev) return 0.5;
  if ('mate' in ev && ev.mate !== null && ev.mate !== undefined) {
    if (ev.mate === 0) return ev.winner === 'black' ? 0 : 1;
    return ev.mate > 0 ? 1 : 0;
  }
  const cp = Math.max(-1500, Math.min(1500, ev.cp));
  return 1 / (1 + Math.exp(-0.00368208 * cp));
}

export class EvalBar {
  constructor(root) {
    this.root = root;
    root.classList.add('evalbar');
    root.innerHTML = '<div class="fill"></div><div class="label top"></div><div class="label bottom"></div><div class="na hidden">eval unavailable</div>';
  }
  // flipped: Black at the bottom (the board is seen from Black's side)
  set(ev, available, flipped = false) {
    const r = this.root;
    r.classList.toggle('flipped', flipped);
    if (available === false && !ev) {
      r.classList.add('unavailable');
      r.title = 'Stockfish unavailable';
      r.querySelector('.na').classList.remove('hidden');
      r.querySelectorAll('.label').forEach(l => (l.textContent = ''));
      return;
    }
    r.classList.remove('unavailable');
    r.querySelector('.na').classList.add('hidden');
    if (!ev) return; // keep the previous value while analysis is pending
    const share = whiteShare(ev);
    r.querySelector('.fill').style.height = (share * 100).toFixed(1) + '%';
    const txt = evalText(ev);
    const nearSide = (share >= 0.5) !== flipped;    // the side at the bottom is ahead
    r.querySelector('.label.bottom').textContent = nearSide ? txt : '';
    r.querySelector('.label.top').textContent = nearSide ? '' : txt;
    r.title = 'Stockfish: ' + txt + (ev.depth ? ` (depth ${ev.depth})` : '');
  }
}

export class EvalChart {
  constructor(canvas, onClick) {
    this.onClick = onClick;
    const css = getComputedStyle(document.documentElement);
    const muted = css.getPropertyValue('--muted').trim() || '#888';
    const accent = css.getPropertyValue('--accent').trim() || '#36c';
    this.chart = new window.Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets: [{ data: [], borderColor: accent, borderWidth: 2, pointRadius: 0,
        pointHoverRadius: 4, tension: 0.2, fill: { target: 'origin', above: 'rgba(240,240,240,.35)', below: 'rgba(40,40,40,.35)' },
        spanGaps: true }] },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: { min: -10, max: 10, ticks: { color: muted, stepSize: 5 }, grid: { color: c => (c.tick.value === 0 ? muted : 'transparent') } },
          x: { ticks: { color: muted, maxTicksLimit: 10 }, grid: { display: false } },
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          title: items => (items[0].dataIndex === 0 ? 'Start' : `Ply ${items[0].dataIndex}`),
          label: item => this.texts[item.dataIndex] ?? '',
        } } },
        onClick: (e, els) => {
          const pts = this.chart.getElementsAtEventForMode(e, 'index', { intersect: false }, false);
          if (pts.length && this.onClick) this.onClick(pts[0].index);
        },
      },
      plugins: [{
        id: 'cursor',
        afterDraw: chart => {
          if (this.cursor == null) return;
          const x = chart.scales.x.getPixelForValue(this.cursor);
          const { top, bottom } = chart.chartArea, ctx = chart.ctx;
          ctx.save(); ctx.strokeStyle = muted; ctx.setLineDash([3, 3]);
          ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
        },
      }],
    });
  }
  set(evals, cursor) {
    this.texts = evals.map(evalText);
    this.chart.data.labels = evals.map((_, i) => i);
    this.chart.data.datasets[0].data = evals.map(evalPawns);
    this.cursor = cursor;
    this.chart.update();
  }
}

// ---------------------------------------------------------------- move classification
const svg = body => `<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
const glyph = t => svg(`<text x="12" y="${t.length > 1 ? 17.4 : 18}" text-anchor="middle" ` +
  `font-size="${t.length > 1 ? 15.5 : 18}" font-weight="900" font-family="Arial, system-ui, sans-serif" ` +
  `fill="#fff" letter-spacing="${t.length > 1 ? -1.6 : 0}">${t}</text>`);
const path = d => svg(`<path transform="translate(4.2 4.2) scale(.65)" fill="#fff" d="${d}"/>`);
export const CLASS = {
  brilliant: { name: 'Brilliant', color: '#3bb9e8', icon: glyph('!!') },
  great: { name: 'Great move', color: '#1fa38a', icon: glyph('!') },
  best: { name: 'Best move', color: '#2e8b3e',
    icon: path('M12 17.27 18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z') },
  excellent: { name: 'Excellent', color: '#5cb85c',
    icon: path('M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z') },
  good: { name: 'Good', color: '#8fc27e',
    icon: path('M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z') },
  inaccuracy: { name: 'Inaccuracy', color: '#e6b422', icon: glyph('?!') },
  mistake: { name: 'Mistake', color: '#e05555',
    icon: path('M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z') },
  blunder: { name: 'Blunder', color: '#9b1c1c', icon: glyph('??') },
};

export function classTitle(c) {
  if (!c || !CLASS[c.label]) return '';
  const tags = { sacrifice: 'sacrifice', fork: 'fork', only_move: 'only good move', trap: 'trap', forced: 'only legal move' };
  const extra = (c.tags || []).map(t => tags[t]).filter(Boolean);
  const bad = ['inaccuracy', 'mistake', 'blunder', 'good', 'excellent'].includes(c.label);
  return CLASS[c.label].name + (extra.length ? ` (${extra.join(', ')})` : '') +
    (c.loss ? ` · −${c.loss.toFixed(1)}% win chance` : '') +
    (bad && c.best_san && c.rank !== 1 ? ` · best was ${c.best_san}` : '');
}

// Small round badge with the classification symbol (move list, legends).
export function classIcon(c) {
  const k = CLASS[c?.label];
  return k ? `<span class="cls-ic" style="background:${k.color}" title="${esc(classTitle(c))}">${k.icon}</span>` : '';
}

// ---------------------------------------------------------------- board
const PROMO = ['queen', 'knight', 'rook', 'bishop'];
const ROLE_LETTER = { queen: 'q', knight: 'n', rook: 'r', bishop: 'b' };

export class Board {
  constructor(root, opts = {}) {
    this.root = root;
    this.orientation = 'white';
    // Created interactive so chessground binds its document-level drag handlers
    // (it only does that at creation), then made view-only until setInteractive().
    this.cg = Chessground(root, {
      viewOnly: false, coordinates: opts.coordinates ?? true,
      animation: { enabled: true, duration: 220 }, highlight: { lastMove: true, check: true },
      orientation: 'white', drawable: { enabled: false },
      movable: { free: false, showDests: true, events: { after: (o, d) => this._after(o, d) } },
      draggable: { enabled: true, showGhost: true },
      premovable: { enabled: false },
    });
    this.cg.set({ viewOnly: true });
    this.cg.redrawAll();
    // classification marks live outside chessground's DOM so its redraws keep them
    if (opts.overlay !== false) {
      this.layer = document.createElement('div');
      this.layer.className = 'cls-layer';
      root.parentElement.appendChild(this.layer);
    }
    this.mark = null;
  }

  set(fen, lastUci, inCheck, force = false) {
    const key = `${fen}|${lastUci}|${inCheck}`;
    if (key === this.key && !force) return;   // keep a drag or selection in progress
    this.key = key;
    this.last = [fen, lastUci, inCheck];
    this.fen = fen;
    const turn = turnFromFen(fen);
    this.cg.set({
      fen, turnColor: turn,
      lastMove: lastUci ? [lastUci.slice(0, 2), lastUci.slice(2, 4)] : undefined,
      check: inCheck ? turn : false,
    });
  }

  setOrientation(color) {
    if (color === this.orientation) return;
    this.orientation = color;
    this.cg.set({ orientation: color });
    this._drawMark(false);
  }

  // opt: {color, dests: {e2: ['e3', 'e4'], ...}, onMove(uci)} lets that color move; null = view only
  setInteractive(opt) {
    const key = opt ? `${opt.color}|${this.fen}` : null;
    if (key === this.interactiveKey) return;
    this.interactiveKey = key;
    this.onMove = opt?.onMove || null;
    this._closePromo();
    const wasViewOnly = this.cg.state.viewOnly;
    if (opt) {
      this.cg.set({ viewOnly: false, turnColor: opt.color,
        movable: { color: opt.color, dests: new Map(Object.entries(opt.dests || {})) } });
    } else {
      this.cg.selectSquare(null);
      this.cg.set({ viewOnly: true, movable: { color: undefined, dests: new Map() } });
    }
    // chessground binds (or drops) its mouse handlers only when it rebuilds the board
    if (wasViewOnly !== this.cg.state.viewOnly) this.cg.redrawAll();
  }

  // Back to the last position given to set() (after a rejected or cancelled move).
  reset() {
    this._closePromo();
    if (this.last) this.set(...this.last, true);
    this.interactiveKey = undefined;     // re-apply the movable config on the next setInteractive
  }

  _after(orig, dest) {
    const piece = this.cg.state.pieces.get(dest);
    if (piece?.role === 'pawn' && (dest[1] === '8' || dest[1] === '1')) return this._promo(orig, dest, piece.color);
    this.onMove?.(orig + dest);
  }

  // square name -> {col, row} on screen (0..7 from the top-left)
  _pos(sq) {
    const f = sq.charCodeAt(0) - 97, r = +sq[1] - 1;
    return this.orientation === 'white' ? { col: f, row: 7 - r } : { col: 7 - f, row: r };
  }

  // chess.com-style picker: the four pieces in a column on the promotion file
  _promo(orig, dest, color) {
    this._closePromo();
    const { col, row } = this._pos(dest);
    const down = row === 0;                         // grow away from the board edge
    const el = document.createElement('div');
    el.className = 'promo';
    el.innerHTML = PROMO.map((role, i) => `<button type="button" data-role="${role}" title="${role}"
      style="left:${col * 12.5}%;top:${(down ? row + i : row - i) * 12.5}%"><piece class="${role} ${color}"></piece></button>`).join('');
    el.addEventListener('click', e => {
      const role = e.target.closest('button')?.dataset.role;
      this._closePromo();
      if (role) this.onMove?.(orig + dest + ROLE_LETTER[role]);
      else this.reset();
    });
    this.promoKey = e => { if (e.key === 'Escape') { this._closePromo(); this.reset(); } };
    window.addEventListener('keydown', this.promoKey);
    this.root.appendChild(el);
    this.promoEl = el;
  }

  _closePromo() {
    this.promoEl?.remove(); this.promoEl = null;
    if (this.promoKey) window.removeEventListener('keydown', this.promoKey);
    this.promoKey = null;
  }

  // Classification of the move that landed on `sq`: a full-square flash with a big
  // symbol (animate) that fades to a badge on the square's top-right corner.
  showMark(sq, label, animate = false) {
    if (!this.layer) return;
    const key = `${sq}|${label}`;
    if (this.mark?.key === key && !animate) return;
    this.mark = { key, sq, label };
    this._drawMark(animate);
  }

  clearMark() {
    if (!this.layer || !this.mark) return;
    this.mark = null;
    this.layer.innerHTML = '';
  }

  _drawMark(animate) {
    if (!this.layer) return;
    this.layer.innerHTML = '';
    const m = this.mark, k = m && CLASS[m.label];
    if (!k) return;
    const { col, row } = this._pos(m.sq);
    const at = `left:${col * 12.5}%;top:${row * 12.5}%`;
    if (animate) {
      const flash = document.createElement('div');
      flash.className = 'cls-flash';
      flash.style.cssText = `${at};--c:${k.color}`;
      flash.innerHTML = `<span class="cls-big" style="background:${k.color}">${k.icon}</span>`;
      flash.addEventListener('animationend', e => { if (e.target === flash) flash.remove(); });
      this.layer.appendChild(flash);
    }
    const badge = document.createElement('div');
    badge.className = 'cls-badge' + (animate ? ' pop' : '');
    badge.style.cssText = `left:${(col + 1) * 12.5}%;top:${row * 12.5}%;background:${k.color}`;
    badge.title = k.name;
    badge.innerHTML = k.icon;
    this.layer.appendChild(badge);
  }
}

// ---------------------------------------------------------------- frames
// The game as seen after `n` plies (n = 0 is the start position).
export function frameAt(game, n) {
  const plies = game.plies;
  const rec = n > 0 ? plies[n - 1] : null;
  return {
    n,
    fen: rec ? rec.fen_after : game.start_fen,
    lastUci: rec ? rec.uci : null,
    lastSan: rec ? rec.san : null,
    inCheck: !!(rec && /[+#]$/.test(rec.san || '')),
    clocks: rec ? rec.clock_after : { white: 0, black: 0 },
    illegal: rec ? rec.illegal_after : { white: { total: 0, streak: 0 }, black: { total: 0, streak: 0 } },
    tokens: (rec && rec.tokens_after) || { white: null, black: null },
    eval: n === 0 ? game.start_eval : rec.eval,
    evals: [game.start_eval, ...plies.map(p => p.eval)],
  };
}

export function lastKnownEval(evals, n) {
  for (let i = n; i >= 0; i--) if (evals[i]) return evals[i];
  return null;
}

// ---------------------------------------------------------------- player card
export function renderPlayer(root, opts) {
  const { color, side, think, illegal, mat, toMove, thinking, violations, tokens } = opts;
  root.className = 'player' + (toMove ? ' to-move' : '');
  const m = mat[color];
  const human = side.agent.cli === 'human';
  const tool = violations ? `<span class="badge warn" title="tool-use violations">⚠ tools ×${violations}</span>` : '';
  const streak = illegal.streak ? `<span class="badge bad">streak ${illegal.streak}</span>` : '';
  root.innerHTML = `
    <div class="piece-icon ${color}" title="${colorName(color)}">${GLYPH[color]}</div>
    <div class="who">
      <div class="name">${esc(agentName(side))} ${thinking ? `<span class="muted thinking-dots">${human ? 'to move' : 'thinking'}</span>` : ''}</div>
      ${human ? '<div class="sub"><span>human player</span></div>' : `<div class="sub">
        <span title="${esc(side.agent.model || '')}">${esc(side.agent.cli)}${side.agent.model ? ' · ' + esc(side.agent.model) : ''}${side.agent.reasoning_effort ? ' · ' + esc(side.agent.reasoning_effort) : ''}</span>
        <span title="illegal attempts (whole game)">illegal: ${illegal.total}</span> ${streak} ${tool}
      </div>
      <div class="sub">${tokenSpan(tokens)}</div>`}
      <div class="captured">${m.captured.join('')}${m.lead ? `<span class="score">+${m.lead}</span>` : ''}</div>
    </div>
    <div class="clock">${fmtTime(think)}<small>total think</small></div>`;
}

// ---------------------------------------------------------------- move list
export function renderMoves(root, game, selected, onClick) {
  const plies = game.plies;
  const startFull = parseInt(game.start_fen.split(' ')[5] || '1', 10);
  const blackFirst = turnFromFen(game.start_fen) === 'black';
  const rows = [];
  let row = null;
  plies.forEach((p, i) => {
    if (p.color === 'white' || !row) {
      const num = startFull + Math.floor((i + (blackFirst ? 1 : 0)) / 2);
      row = { num, white: null, black: null };
      rows.push(row);
    }
    row[p.color] = i + 1;
  });
  const cell = n => {
    if (!n) return '<td></td>';
    const p = plies[n - 1];
    if (p.book) {
      return `<td class="mv book${n === selected ? ' sel' : ''}" data-ply="${n}" title="book move (opening set by the referee)">` +
        `${esc(p.san)}<span class="t">book</span></td>`;
    }
    const rej = Math.max(0, p.attempts.length - 1);
    return `<td class="mv${n === selected ? ' sel' : ''}" data-ply="${n}" title="think ${fmtTime(p.think_s)}">${esc(p.san)}` +
      (rej ? `<span class="x" title="${rej} rejected attempt(s)">✗${rej}</span>` : '') +
      (p.tool_violations?.length ? '<span class="x" title="tool use">⚠</span>' : '') +
      `<span class="t">${fmtTime(p.think_s)}</span>${classIcon(p.classification)}</td>`;
  };
  root.innerHTML = rows.length
    ? `<table>${rows.map(r => `<tr><td class="num">${r.num}.</td>${cell(r.white)}${cell(r.black)}</tr>`).join('')}</table>`
    : '<div class="muted">No moves yet.</div>';
  root.querySelectorAll('td.mv').forEach(td => td.addEventListener('click', () => onClick(+td.dataset.ply)));
  // keep the selected move visible by scrolling the list only (never the page)
  const sel = root.querySelector('td.sel');
  if (sel) {
    const top = sel.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop;
    const bottom = top + sel.offsetHeight;
    if (top < root.scrollTop) root.scrollTop = top;
    else if (bottom > root.scrollTop + root.clientHeight) root.scrollTop = bottom - root.clientHeight;
  }
}

// ---------------------------------------------------------------- events
const EVENT_TEXT = {
  illegal_attempt: e => [`${colorName(e.color)} attempt ${e.attempt} rejected: ${e.reason}`, 'bad'],
  tool_use_violation: e => [`⚠ ${colorName(e.color)} used a tool: ${JSON.stringify(e.events).slice(0, 200)}`, 'warn'],
  session_resume_failure: e => [`${colorName(e.color)}: session resume failed, started a new session`, 'warn'],
  agent_unresponsive: e => [`${colorName(e.color)} unresponsive: ${e.reason}`, 'warn'],
  agent_warning: e => [`${colorName(e.color)}: ${e.warning}`, 'warn'],
  referee_error: e => [`Referee error: ${e.error}`, 'bad'],
};
export function renderEvents(root, events) {
  if (!events.length) { root.innerHTML = '<div class="muted">Nothing unusual yet.</div>'; return; }
  root.innerHTML = events.slice().reverse().map(e => {
    const [txt, cls] = (EVENT_TEXT[e.type] || (x => [x.type, '']))(e);
    return `<div class="${cls}"><span class="muted">ply ${e.ply} · ${esc((e.time || '').slice(11))}</span> ${esc(txt)}</div>`;
  }).join('');
}

// ---------------------------------------------------------------- resignation panel
export class ResignPanel {
  constructor() { this.timer = null; }
  open(game) {
    const r = game.resignation;
    if (!r) return;
    this.close();
    const steps = [{ fen: r.fen, uci: null, san: null, check: false }, ...(r.line || [])];
    const bg = document.createElement('div');
    bg.className = 'modal-bg';
    bg.innerHTML = `<div class="modal" role="dialog" aria-label="Resignation">
      <h2>${GLYPH[r.color]} ${esc(agentName(game[r.color]))} resigned</h2>
      <p>${esc(r.explanation || '(no explanation)')}</p>
      <div class="muted">Expected line:</div>
      <div class="line-moves"></div>
      <div class="resign-board"></div>
      <div class="player-controls">
        <button data-a="first" title="Start">⏮</button><button data-a="prev" title="Back">◀</button>
        <button data-a="play" title="Autoplay">▶ Play</button>
        <button data-a="next" title="Forward">▶</button><button data-a="last" title="End">⏭</button>
        <span style="flex:1"></span><button data-a="close">Close</button>
      </div></div>`;
    document.body.appendChild(bg);
    this.bg = bg;
    const board = new Board(bg.querySelector('.resign-board'), { coordinates: false, overlay: false });
    const movesEl = bg.querySelector('.line-moves');
    let i = 0;
    const show = () => {
      const s = steps[i];
      board.set(s.fen, s.uci, s.check);
      movesEl.innerHTML = steps.slice(1).map((s2, k) => `<span class="${k + 1 === i ? 'cur' : ''}">${esc(s2.san)}</span>`).join(' ');
    };
    const stop = () => { clearInterval(this.timer); this.timer = null; bg.querySelector('[data-a=play]').textContent = '▶ Play'; };
    const act = a => {
      if (a === 'close') return this.close();
      if (a === 'play') {
        if (this.timer) return stop();
        if (i >= steps.length - 1) i = 0;
        show();
        bg.querySelector('[data-a=play]').textContent = '⏸ Pause';
        this.timer = setInterval(() => { if (i >= steps.length - 1) return stop(); i++; show(); }, 900);
        return;
      }
      stop();
      if (a === 'first') i = 0;
      if (a === 'prev') i = Math.max(0, i - 1);
      if (a === 'next') i = Math.min(steps.length - 1, i + 1);
      if (a === 'last') i = steps.length - 1;
      show();
    };
    bg.addEventListener('click', e => {
      if (e.target === bg) return this.close();
      const a = e.target.closest('button')?.dataset.a;
      if (a) act(a);
    });
    this.keyHandler = e => {
      if (e.key === 'Escape') this.close();
      if (e.key === 'ArrowRight') { act('next'); e.stopImmediatePropagation(); e.preventDefault(); }
      if (e.key === 'ArrowLeft') { act('prev'); e.stopImmediatePropagation(); e.preventDefault(); }
    };
    window.addEventListener('keydown', this.keyHandler, true);
    show();
    setTimeout(() => act('play'), 500);
  }
  get isOpen() { return !!this.bg; }
  close() {
    clearInterval(this.timer); this.timer = null;
    if (this.keyHandler) window.removeEventListener('keydown', this.keyHandler, true);
    this.bg?.remove(); this.bg = null;
  }
}

// ---------------------------------------------------------------- agent console launcher
// mode: 'browser' opens /console in a pop-out window; 'terminal' asks the server to
// open a console window running `python -m arena watch <side>`. game=null follows live.
export function consoleControls(root, gameId = null) {
  let mode = 'browser';
  try { mode = localStorage.getItem('arena.consoleMode') || 'browser'; } catch { /* ignore */ }
  root.innerHTML = `<div class="controls" style="margin-top:0;align-items:center">
      <select class="cmode" title="where the agent console opens">
        <option value="browser">Browser window</option><option value="terminal">Terminal window</option></select>
      <button data-side="white">♔ Watch White</button><button data-side="black">♚ Watch Black</button>
      <span class="muted cmsg" style="font-size:12px"></span></div>
    <div class="muted" style="font-size:12px;margin-top:6px">Prompts sent, streamed replies, reasoning summaries (Codex),
      tool-call attempts, verdicts and tokens, live. Claude Code does not expose its thinking text.</div>`;
  const sel = root.querySelector('.cmode');
  sel.value = mode;
  sel.onchange = () => { try { localStorage.setItem('arena.consoleMode', sel.value); } catch { /* ignore */ } };
  root.querySelectorAll('button[data-side]').forEach(b => b.onclick = async () => {
    const side = b.dataset.side, msg = root.querySelector('.cmsg');
    if (sel.value === 'browser') {
      const url = `/console?side=${side}` + (gameId ? `&game=${encodeURIComponent(gameId)}` : '');
      const w = window.open(url, `arena-console-${side}${gameId ? '-' + gameId : ''}`, 'width=780,height=920');
      msg.textContent = w ? '' : 'Pop-up blocked: allow pop-ups for this page.';
    } else {
      const r = await fetch('/api/console/terminal', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side, game: gameId }) });
      msg.textContent = r.ok ? `Terminal opened for ${side}.` : 'Could not open a terminal.';
      setTimeout(() => { msg.textContent = ''; }, 4000);
    }
  });
}

// ---------------------------------------------------------------- result text
export function resultBanner(game) {
  const r = game.result;
  const head = r === '1-0' ? 'White wins' : r === '0-1' ? 'Black wins' : r === '1/2-1/2' ? 'Draw' : 'No result';
  const who = game.winner ? ` — ${esc(agentName(game[game.winner]))}` : '';
  return `<strong>${r === '*' ? '' : esc(r) + ' · '}${head}</strong>${who}<div class="muted">${esc(game.reason || '')}</div>`;
}
