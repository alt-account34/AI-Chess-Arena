// Live agent console (browser window). Read-only view of one side's CLI activity.
import { esc, fmtTokens } from './view.js';

const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);
const side = params.get('side') === 'black' ? 'black' : 'white';
const pinnedGame = params.get('game');            // null = follow the live game
const log = $('#log');
let reply = null;                                  // the reply block currently streaming
let gameId = null;

$('#icon').className = `piece-icon ${side}`;
$('#icon').textContent = side === 'white' ? '♔' : '♚';
document.title = `${side === 'white' ? 'White' : 'Black'} · agent console`;

// options (remembered per viewer)
const opt = (id, cls, invert) => {
  const el = $(id);
  try { const v = localStorage.getItem('console' + id); if (v !== null) el.checked = v === '1'; } catch { /* ignore */ }
  const apply = () => {
    document.body.classList.toggle(cls, invert ? !el.checked : el.checked);
    try { localStorage.setItem('console' + id, el.checked ? '1' : '0'); } catch { /* ignore */ }
  };
  el.onchange = apply; apply();
};
opt('#opt-raw', 'hide-raw', true);
opt('#opt-prompts', 'hide-prompts', true);

const ts = t => new Date((t || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour12: false });
const tok = t => (t ? `${fmtTokens(t.input)} in (${fmtTokens(t.cached)} cached) / ${fmtTokens(t.output)} out` : '');

function add(ev, cls, html, extraClass = '') {
  log.querySelector('.empty')?.remove();
  const div = document.createElement('div');
  div.className = `ev ${extraClass}`;
  div.innerHTML = `<span class="ts">${ts(ev.t)}</span><span class="body ${cls}">${html}</span>`;
  log.appendChild(div);
  return div.querySelector('.body');
}
function endReply() { if (reply) { reply.classList.remove('streaming'); reply = null; } }
function scroll() { if ($('#opt-scroll').checked) window.scrollTo(0, document.body.scrollHeight); }

async function header(gid) {
  if (!gid) { $('#who').textContent = 'No game yet'; return; }
  let g = null;
  try {
    const cur = await fetch('/api/game/current').then(r => r.json());
    g = cur && cur.id === gid ? cur : await fetch(`/api/games/${encodeURIComponent(gid)}`).then(r => (r.ok ? r.json() : null));
  } catch { /* ignore */ }
  const a = g?.[side]?.agent;
  $('#who').textContent = a ? `${a.display_name || a.id} · ${side === 'white' ? 'White' : 'Black'}` : `${side} · ${gid}`;
  $('#sub').textContent = a ? [a.cli, a.model, a.reasoning_effort].filter(Boolean).join(' · ') : '';
}

function handle(ev) {
  const k = ev.k;
  if (k === 'raw') {             // raw lines interleave with the stream; they must not split a reply
    add(ev, 'raw', esc(ev.line), 'raw-ev');
    return scroll();
  }
  if (k === 'text') {
    if (!reply) {
      reply = add(ev, 'reply streaming', '');
    }
    reply.textContent += ev.delta || '';
  } else if (k === 'block_end') {
    endReply();
  } else {
    endReply();
    if (k === 'turn') {
      log.querySelector('.empty')?.remove();
      const d = document.createElement('div');
      d.className = 'turn';
      d.textContent = `ply ${ev.ply} · move ${ev.move_number} · ${side === 'white' ? 'White' : 'Black'} to play`;
      log.appendChild(d);
    } else if (k === 'prompt') {
      add(ev, 'prompt', `<details><summary>→ prompt sent (attempt ${ev.attempt}, ${(ev.text || '').length} chars${ev.new_session ? ', new session' : ''})</summary><pre>${esc(ev.text)}</pre></details>`, 'prompt-ev');
    } else if (k === 'session') {
      const tools = ev.tools !== undefined ? ` · tools: ${ev.tools && ev.tools.length ? esc(ev.tools.join(', ')) : 'none'}` : '';
      add(ev, 'dim', `session ${esc(String(ev.session_id || '').slice(0, 13))}${ev.model ? ' · ' + esc(ev.model) : ''}${tools}`);
    } else if (k === 'thinking') {
      add(ev, 'thinking', '… thinking');
    } else if (k === 'thinking_done') {
      add(ev, 'thinking', `… thinking finished${ev.redacted ? ' <span class="dim">(Claude Code does not expose thinking text)</span>' : ''}`);
    } else if (k === 'reasoning') {
      add(ev, 'reasoning', `◆ reasoning summary\n${esc(ev.text)}`);
    } else if (k === 'message') {
      add(ev, 'reply', esc(ev.text));
    } else if (k === 'tool') {
      add(ev, 'warn', `⚠ tool call attempt: ${esc(ev.tool)} ${esc(String(ev.input || ev.detail || '').slice(0, 300))}`);
    } else if (k === 'notice') {
      add(ev, 'warn', `! ${esc(ev.text)}`);
    } else if (k === 'result') {
      const t = tok(ev.tokens);
      if (ev.accepted) add(ev, 'ok', `✓ ${esc(ev.move)} accepted · ${(ev.think_s || 0).toFixed(1)}s${t ? ' · ' + t : ''}`);
      else add(ev, 'bad', `✗ ${esc(ev.move || 'no move')} rejected: ${esc(ev.reason)} · ${(ev.think_s || 0).toFixed(1)}s${t ? ' · ' + t : ''}`);
      for (const tl of ev.tools || []) add(ev, 'warn', `⚠ tool-use violation: ${esc(tl.tool)} ${esc(String(tl.input || tl.detail || '').slice(0, 200))}`);
    } else if (k === 'status') {
      add(ev, ev.final ? 'ok' : 'warn', `■ ${esc(ev.text)}`);
    }
  }
  scroll();
}

function connect() {
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/console?side=${side}` +
    (pinnedGame ? `&game=${encodeURIComponent(pinnedGame)}` : '');
  const ws = new WebSocket(url);
  ws.onopen = () => { $('#conn').className = 'conn ok'; $('#conn').textContent = pinnedGame ? 'replay' : 'live'; };
  ws.onmessage = async m => {
    const ev = JSON.parse(m.data);
    if (ev.k === 'history') {
      log.innerHTML = ''; reply = null; gameId = ev.game;
      await header(gameId);
      if (!ev.events.length) {
        log.innerHTML = `<div class="empty">${gameId ? (pinnedGame ? 'No console log was recorded for this game (games played before the console existed have none).' : 'Waiting for the agent…') : 'No game yet. Start one from the setup page; this window will follow it.'}</div>`;
      }
      ev.events.forEach(handle);
      endReply();
    } else if (ev.k === 'new_game') {
      log.innerHTML = '<div class="empty">New game starting…</div>'; reply = null; gameId = ev.game;
      header(gameId);
    } else {
      handle(ev);
    }
  };
  ws.onclose = () => {
    $('#conn').className = 'conn down'; $('#conn').textContent = 'reconnecting…';
    setTimeout(connect, 2000);
  };
}
connect();
