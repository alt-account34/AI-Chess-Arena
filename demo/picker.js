import { readSpeed, saveSpeed, fetchJSON } from './shared.mjs';
const $ = s => document.querySelector(s);
$('#speed').value = String(readSpeed());
$('#speed').onchange = () => saveSpeed(Number($('#speed').value));
try {
  const games = await fetchJSON('data/manifest.json');
  for (const color of ['white', 'black']) {
    const models = new Map(games.map(g => [g[color + '_id'], g[color + '_name']]));
    for (const [id, name] of [...models].sort((a, b) => a[1].localeCompare(b[1]))) {
      $('#' + color).add(new Option(name, id));
    }
    $('#' + color).value = games[0][color + '_id'];
    $('#' + color).onchange = filter;
  }
  function filter() {
    const previous = $('#games').value;
    const matches = games.filter(g => (!$('#white').value || g.white_id === $('#white').value) && (!$('#black').value || g.black_id === $('#black').value));
    $('#games').replaceChildren(...matches.map(g => new Option(`${g.white_name} vs ${g.black_name} · ${g.started_at.replace('T', ' ')} · ${g.opening}`, g.id)));
    if (matches.some(g => g.id === previous)) $('#games').value = previous;
    $('#start').disabled = !matches.length;
    $('#games').disabled = !matches.length;
    details();
  }
  function details() {
    const g = games.find(g => g.id === $('#games').value);
    $('#details').textContent = g ? `${g.plies} plies · ${g.opening} · Legal moves ${g.legal_moves_sent ? 'on' : 'off'} · Think aloud ${g.think_aloud ? 'on' : 'off'}` : 'No saved games for this matchup. Swap colors or show all games.';
  }
  $('#games').onchange = details;
  $('#reset').onclick = () => { $('#white').value = ''; $('#black').value = ''; filter(); };
  $('#swap').onclick = () => {
    const w = $('#white').value, b = $('#black').value;
    // Both filters include every recorded model, including models with only one color.
    $('#white').value = b; $('#black').value = w; filter();
  };
  // Include all models in both selectors so swapping never silently clears a model.
  const all = new Map(games.flatMap(g => [[g.white_id, g.white_name], [g.black_id, g.black_name]]));
  for (const color of ['white', 'black']) for (const [id, name] of all) {
    if (![...$('#' + color).options].some(o => o.value === id)) $('#' + color).add(new Option(name, id));
  }
  $('#form').onsubmit = e => {
    e.preventDefault();
    if ($('#games').value) location.href = `watch.html?id=${encodeURIComponent($('#games').value)}`;
  };
  filter();
} catch (e) {
  $('#details').textContent = '';
  $('#error').textContent = 'Could not load the saved games. Please reload to try again.';
}
