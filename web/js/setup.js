const $ = s => document.querySelector(s);

const [agents, settings, openings] = await Promise.all([
  fetch('/api/agents').then(r => r.json()),
  fetch('/api/settings').then(r => r.json()),
  fetch('/api/openings').then(r => r.json()),
]);

// opening picker: free / random / custom / grouped book lines
const groups = [...new Set(openings.map(o => o.group))];
$('#opening').innerHTML =
  '<option value="free">Free: White picks its own opening</option>' +
  '<option value="random">Random named opening</option>' +
  '<option value="custom">Custom moves…</option>' +
  groups.map(g => `<optgroup label="${g}">` + openings.filter(o => o.group === g)
    .map(o => `<option value="${o.id}">${o.name}${o.eco ? ' · ' + o.eco : ''}</option>`).join('') + '</optgroup>').join('');
$('#opening').onchange = () => {
  const v = $('#opening').value, o = openings.find(x => x.id === v);
  $('#custom-wrap').classList.toggle('hidden', v !== 'custom');
  $('#opening-help').textContent =
    v === 'free' ? "White's agent chooses its own first move and the game develops freely." :
    v === 'random' ? 'A named opening is picked at random when the game starts; the referee plays its moves.' :
    v === 'custom' ? 'The referee plays these moves for both sides, then the agents take over.' :
    `The referee plays ${o.line} as book moves, then the agents take over.`;
};

const describe = a => a.cli === 'human' ? 'you play on the board in the browser'
  : [a.cli, a.model, a.reasoning_effort].filter(Boolean).join(' · ');
const GROUP_ORDER = ['Human', 'Claude', 'Codex', 'Mock'];
const groupOf = a => a.group || a.cli;
const sortedAgents = [...agents].sort((x, y) => {
  const gx = GROUP_ORDER.indexOf(groupOf(x)), gy = GROUP_ORDER.indexOf(groupOf(y));
  return (gx < 0 ? 99 : gx) - (gy < 0 ? 99 : gy);     // stable: keeps the agents.yaml order within a group
});

// Searchable model picker: a button that opens a popover with a filter box and a
// grouped list. "sol hi" matches "GPT-6 Sol (high)": every word must appear somewhere.
class Picker {
  constructor(root, onChange) {
    this.root = root;
    this.onChange = onChange;
    root.innerHTML = `<button type="button" class="pick-btn"><span class="pick-name"></span><small class="pick-desc"></small></button>
      <div class="pick-pop hidden"><input type="text" placeholder="Search models: sonnet high, luna, player…" autocomplete="off">
      <div class="pick-list" role="listbox"></div></div>`;
    this.btn = root.querySelector('.pick-btn');
    this.pop = root.querySelector('.pick-pop');
    this.input = root.querySelector('input');
    this.list = root.querySelector('.pick-list');
    this.btn.onclick = () => (this.pop.classList.contains('hidden') ? this.open() : this.close());
    this.input.oninput = () => { this.active = 0; this.draw(); };
    this.input.onkeydown = e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        this.active = Math.max(0, Math.min(this.matches.length - 1, this.active + (e.key === 'ArrowDown' ? 1 : -1)));
        this.draw(true);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (this.matches[this.active]) this.choose(this.matches[this.active].id);
      } else if (e.key === 'Escape') {
        e.preventDefault(); this.close(); this.btn.focus();
      }
    };
    this.list.addEventListener('mousedown', e => {
      const it = e.target.closest('.pick-item');
      if (it) { e.preventDefault(); this.choose(it.dataset.id); }
    });
    document.addEventListener('mousedown', e => { if (!root.contains(e.target)) this.close(); });
  }
  get value() { return this.id; }
  set value(id) {
    this.id = id;
    const a = agents.find(x => x.id === id) || {};
    this.root.querySelector('.pick-name').textContent = a.display_name || id;
    this.root.querySelector('.pick-desc').textContent = describe(a);
  }
  open() {
    for (const p of pickers) if (p !== this) p.close();
    this.pop.classList.remove('hidden');
    this.input.value = '';
    this.active = 0;
    this.draw();
    const cur = this.matches.findIndex(a => a.id === this.id);
    if (cur >= 0) { this.active = cur; this.draw(true); }
    this.input.focus();
  }
  close() { this.pop.classList.add('hidden'); }
  choose(id) { this.value = id; this.close(); this.btn.focus(); this.onChange?.(id); }
  draw(scroll = false) {
    const words = this.input.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hay = a => [a.display_name, a.id, a.model, a.reasoning_effort, a.cli, a.family, groupOf(a)]
      .filter(Boolean).join(' ').toLowerCase();
    this.matches = sortedAgents.filter(a => words.every(w => hay(a).includes(w)));
    let html = '', group = null;
    this.matches.forEach((a, i) => {
      const g = groupOf(a) + (a.family && groupOf(a) !== 'Human' ? ` · ${a.family}` : '');
      if (g !== group) { group = g; html += `<div class="pick-group">${esc(g)}</div>`; }
      html += `<div class="pick-item${i === this.active ? ' active' : ''}${a.id === this.id ? ' cur' : ''}" role="option" data-id="${esc(a.id)}">` +
        `<span>${esc(a.display_name)}</span><small>${esc(a.cli === 'human' ? 'human' : a.model || a.cli)}</small></div>`;
    });
    this.list.innerHTML = html || '<div class="pick-none">No model matches.</div>';
    if (scroll) this.list.querySelector('.pick-item.active')?.scrollIntoView({ block: 'nearest' });
  }
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pickers = [];
const white = new Picker($('#white')), black = new Picker($('#black'));
pickers.push(white, black);

// defaults: the planned match (Astra medium vs Opus medium) when present,
// else a Codex agent vs a Claude agent, else the first two entries
const has = id => agents.some(a => a.id === id);
const firstOf = cli => agents.find(a => a.cli === cli);
white.value = has('astra-medium') ? 'astra-medium' : (firstOf('codex') || agents[0]).id;
black.value = has('opus-5.5-medium') ? 'opus-5.5-medium' : (firstOf('claude_code') || agents[1] || agents[0]).id;
$('#legal').checked = settings.send_legal_moves;
$('#think').checked = !!settings.think_aloud;
$('#cap').value = settings.move_cap_plies;
$('#running').classList.toggle('hidden', !settings.running);

$('#swap').onclick = () => { const w = white.value; white.value = black.value; black.value = w; };

$('#form').onsubmit = async e => {
  e.preventDefault();
  $('#err').textContent = '';
  $('#start').disabled = true;
  const body = {
    white: white.value, black: black.value,
    send_legal_moves: $('#legal').checked, move_cap: parseInt($('#cap').value, 10) || null,
    start_fen: $('#fen').value.trim() || null,
    opening: $('#opening').value,
    think_aloud: $('#think').checked,
    opening_moves: $('#opening').value === 'custom' ? $('#opening-moves').value : null,
  };
  const r = await fetch('/api/game/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (r.ok) { location.href = '/'; return; }
  const d = await r.json().catch(() => ({}));
  $('#err').textContent = d.detail ? (typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)) : `Error ${r.status}`;
  $('#start').disabled = false;
};
