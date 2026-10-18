import {
  $, esc, other, agentName, material, frameAt, lastKnownEval, turnFromFen, fmtTime,
  Board, EvalBar, EvalChart, renderPlayer, renderMoves, renderEvents, resultBanner,
} from './static/js/view.js';
import { timeline, positionAt, Playback } from './timeline.mjs';
import { readSpeed, saveSpeed, fetchJSON } from './shared.mjs';

async function main() {
  const id = new URLSearchParams(location.search).get('id');
  const manifest = await fetchJSON('data/manifest.json');
  if (!manifest.some(g => g.id === id)) throw new Error('Unknown game');
  const game = await fetchJSON(`data/${encodeURIComponent(id)}.json`);
  const steps = timeline(game), duration = steps.at(-1)?.end || 0;
  const player = new Playback(duration, readSpeed());
  const board = new Board($('#board'));
  const bar = new EvalBar($('#evalbar'));
  const chart = new EvalChart($('#chart'), n => seek(n ? steps[n - 1].end : 0));
  const bottom = game.black.agent.cli === 'human' && game.white.agent.cli !== 'human' ? 'black' : 'white';
  board.setOrientation(bottom);
  const engineSeen = [game.start_eval, ...game.plies.map(p => p.eval)].some(Boolean);
  let prompted = false, previousN = -1, last = performance.now(), lastPaint = 0;
  const consoleCache = { white: '', black: '' };
  document.title = `${agentName(game.white)} vs ${agentName(game.black)} — Demo`;
  for (const color of ['white', 'black']) $('#' + color + '-console-title').textContent = `${color === 'white' ? 'White' : 'Black'} · ${agentName(game[color])}`;
  $('#loading').classList.add('hidden');
  $('#game').classList.remove('hidden');
  $('#speed').value = String(player.speed);
  $('#seek').max = String(duration);
  $('#settings-line').textContent = `${game.opening?.name || 'Free opening'} · Legal moves ${game.settings.send_legal_moves ? 'on' : 'off'} · Think aloud ${game.settings.think_aloud ? 'on' : 'off'} · Original thinking times shown`;

  function finish() {
    if ($('#finish').open) return;
    $('#finish-result').innerHTML = resultBanner(game);
    const r = game.resignation;
    $('#resignation').textContent = r ? `${agentName(game[r.color])} resigned. ${r.explanation || ''}${r.line_san?.length ? ' Expected line: ' + r.line_san.join(' ') : ''}` : '';
    $('#finish').showModal();
    $('#finish a').focus();
  }
  $('#close-finish').onclick = () => $('#finish').close();
  $('#show-result').onclick = finish;
  $('#finish').addEventListener('close', () => $('#play').focus());
  $('#finish').onclick = e => { if (e.target === $('#finish')) $('#finish').close(); };

  function syncClock() {
    const now = performance.now();
    player.advance((now - last) / 1000);
    last = now;
  }
  function seek(time) {
    syncClock(); player.playing = false; player.seek(time); render();
  }
  function restart() {
    prompted = false; previousN = -1; player.seek(0); player.playing = true; last = performance.now(); render();
  }
  function toggle() {
    syncClock();
    if (player.time >= duration) return restart();
    player.playing = !player.playing; render();
  }
  $('#play').onclick = toggle;
  $('#restart').onclick = restart;
  $('#prev').onclick = () => seek([...steps].reverse().find(s => s.end < player.time - 0.01)?.end || 0);
  $('#next').onclick = () => seek(steps.find(s => s.end > player.time + 0.01)?.end ?? duration);
  $('#seek').oninput = () => seek(Number($('#seek').value));
  $('#speed').onchange = () => { syncClock(); player.speed = Number($('#speed').value); saveSpeed(player.speed); render(); };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { syncClock(); player.playing = false; render(); }
    last = performance.now();
  });
  window.addEventListener('keydown', e => {
    if ($('#finish').open || e.target?.matches('input, select, textarea, button, a, summary')) return;
    const action = { ' ': toggle, ArrowLeft: () => $('#prev').click(), ArrowRight: () => $('#next').click(), Home: () => seek(0), End: () => seek(duration) }[e.key];
    if (action) { e.preventDefault(); action(); }
  });

  function renderConsoles(state) {
    for (const color of ['white', 'black']) {
      let html = '';
      for (const step of steps) {
        if (step.start > player.time || step.turn.color !== color) continue;
        const progress = player.time >= step.end ? 1 : Math.max(0, (player.time - step.start) / step.duration);
        const t = step.turn;
        html += `<div class="console-turn">Ply ${t.ply}${t.console_source === 'saved responses' ? ' · saved responses' : ''}${t.book ? ' · opening book' : ''}</div>`;
        let streamed = '';
        const flush = () => { if (streamed) html += `<div class="entry">${esc(streamed)}</div>`; streamed = ''; };
        for (const e of t.console || []) {
          if (e.at > progress) break;
          if (e.k === 'text') { streamed += e.delta || ''; continue; }
          flush();
          if (e.k === 'prompt') html += `<details class="prompt"><summary>Prompt · attempt ${esc(e.attempt || 1)}</summary><pre>${esc(e.text)}</pre></details>`;
          else if (e.k === 'message' || e.k === 'reasoning' || e.k === 'notice') html += `<div class="entry">${esc(e.text)}</div>`;
          else if (e.k === 'thinking') html += '<div class="entry muted">Thinking…</div>';
          else if (e.k === 'tool') html += `<div class="entry bad">Tool-use attempt: ${esc(e.tool)}</div>`;
          else if (e.k === 'result') html += `<div class="entry ${e.accepted ? 'good' : 'bad'}">${e.accepted ? '✓' : '✕'} ${esc(e.move || 'Response')} ${e.accepted ? 'accepted' : esc(e.reason || 'rejected')}</div>`;
        }
        flush();
      }
      html ||= '<div class="muted">Waiting for the first turn…</div>';
      if (consoleCache[color] !== html) {
        const root = $('#' + color + '-console');
        const follow = root.scrollHeight - root.scrollTop - root.clientHeight < 40;
        // Preserve expanded prompts when the streamed response updates.
        const expanded = [...root.querySelectorAll('details')].map((d, i) => d.open ? i : -1);
        root.innerHTML = html;
        root.querySelectorAll('details').forEach((d, i) => { d.open = expanded.includes(i); });
        if (follow) root.scrollTop = root.scrollHeight;
        consoleCache[color] = html;
      }
    }
  }

  function render() {
    const state = positionAt(steps, player.time), { n, active, progress } = state;
    const ended = player.time >= duration;
    // Shared renderers receive a truncated snapshot, never the future move list.
    const visible = { ...game, plies: game.plies.slice(0, n) };
    const f = frameAt(visible, n);
    if (n !== previousN) {
      board.set(f.fen, f.lastUci, f.inCheck);
      const move = visible.plies.at(-1);
      if (move?.classification && move.uci) board.showMark(move.uci.slice(2, 4), move.classification.label, n === previousN + 1);
      else board.clearMark();
      bar.set(lastKnownEval(f.evals, n), engineSeen, bottom === 'black');
      chart.set(f.evals, n);
      renderMoves($('#moves'), visible, n, i => seek(i ? steps[i - 1].end : 0));
      previousN = n;
    }
    const mat = material(f.fen), toMove = active?.turn.color || turnFromFen(f.fen);
    for (const [slot, color] of [['#player-top', other(bottom)], ['#player-bottom', bottom]]) {
      let think = f.clocks[color] || 0;
      if (active?.turn.color === color) think += (active.turn.think_s || 0) * progress;
      const final = ended ? game.final_turn : null;
      if (final?.clock_after?.[color] != null) think = final.clock_after[color];
      renderPlayer($(slot), { color, side: game[color], think, mat,
        illegal: final?.illegal_after?.[color] || f.illegal[color] || { total: 0, streak: 0 },
        tokens: final?.tokens_after?.[color] || f.tokens[color],
        violations: state.complete.filter(s => s.turn.color === color).reduce((v, s) => v + (s.turn.tool_violations?.length || 0), 0),
        toMove: !ended && toMove === color, thinking: player.playing && active?.turn.color === color });
    }
    $('#turn-icon').className = `piece-icon ${toMove}`;
    $('#turn-icon').textContent = toMove === 'white' ? '♔' : '♚';
    $('#turn-big').textContent = ended ? `Game over · ${game.result}` : `${toMove === 'white' ? 'White' : 'Black'} to move`;
    $('#turn-sub').textContent = ended ? game.reason || 'Replay complete' : `${agentName(game[toMove])} · ${player.playing ? (active?.turn.book ? 'opening book' : game[toMove].agent.cli === 'human' ? 'recorded human turn' : 'thinking') : 'paused'}`;
    $('#turn-timer').textContent = active ? fmtTime((active.turn.think_s || 0) * progress) : '';
    $('#pos').textContent = `Ply ${n} / ${game.plies.length}`;
    $('#seek').value = String(player.time);
    $('#play').textContent = ended ? 'Replay again' : player.playing ? 'Pause' : 'Resume';
    $('#prev').disabled = player.time <= 0;
    $('#next').disabled = ended;
    $('#banner-end').classList.toggle('hidden', !ended);
    if (ended) $('#end-text').innerHTML = resultBanner(game);
    const events = game.events.filter(e => {
      const step = steps.find(s => s.turn.ply === e.ply);
      if (!step) return ended;
      return player.time >= step.start + step.duration * (e.at ?? 1);
    });
    renderEvents($('#events'), events);
    renderConsoles(state);
    if (ended && !prompted) { prompted = true; finish(); }
  }
  player.playing = !document.hidden;
  render();
  function tick(now) {
    syncClock();
    if (now - lastPaint >= 80) { render(); lastPaint = now; }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
main().catch(error => {
  console.error(error);
  $('#loading').classList.add('hidden');
  $('#game').classList.add('hidden');
  $('#error').classList.remove('hidden');
});
