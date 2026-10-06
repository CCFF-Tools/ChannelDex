/* Private browser draft, preserving paths and metadata through chooser/server failures. */
(function (root) {
  'use strict';
  const modes = ['needs_encoding', 'already_encoded'];
  const suggested = path => path.split('/').pop().replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  function normalize(row) {
    return {source_path: row.source_path || row.path || '', episode_id: 'episode_id' in row ? String(row.episode_id ?? '') : 'new',
      new_title: 'new_title' in row ? String(row.new_title || '') : suggested(row.source_path || row.path || ''),
      episode_number: row.episode_number ?? '', runtime_seconds: row.runtime_seconds ?? '',
      encoding_mode: modes.includes(row.encoding_mode) ? row.encoding_mode :
        ('encode_before_transfer' in row ? (row.encode_before_transfer ? modes[0] : modes[1]) : ''),
      error: row.error || ''};
  }
  function append(rows, entries, episodeId) {
    const result = rows.slice();
    entries.forEach(entry => {
      const path = typeof entry === 'string' ? entry : entry.path;
      if (!result.some(row => row.source_path === path)) {
        result.push(normalize({source_path: path, episode_id: episodeId || 'new', error: entry.error || ''}));
      }
    });
    return result;
  }
  function changeShow(rows, episodes, show) {
    return rows.map(row => ({...row, episode_id: row.episode_id === 'new' || episodes.some(
      ep => String(ep.id) === row.episode_id && String(ep.show_id) === String(show)) ? row.episode_id : ''}));
  }
  const state = {normalize, append, changeShow};
  if (typeof module !== 'undefined' && module.exports) module.exports = state;
  if (!root.document) return;
  const document = root.document, form = document.getElementById('media-queue-form');
  if (!form) return;
  const rowsBox = document.getElementById('queue-rows'), show = document.getElementById('queue-show'),
    target = document.getElementById('queue-target'), status = document.getElementById('choose-media-status'),
    manual = document.getElementById('source-path-manual'), draftInput = document.getElementById('draft-rows'),
    episodes = JSON.parse(document.getElementById('queue-episodes').textContent),
    initial = JSON.parse(document.getElementById('queue-draft').textContent) || [],
    errors = JSON.parse(document.getElementById('queue-errors').textContent) || {}, key = 'channeldex-media-intake';
  let rows = initial.map(normalize);
  const save = () => {
    draftInput.value = JSON.stringify(rows);
    try { root.sessionStorage.setItem(key, JSON.stringify({rows, show: show.value, target: target.value})); } catch (_) {}
  };
  if (form.dataset.accepted === 'true') {
    try { root.sessionStorage.removeItem(key); } catch (_) {}
  } else if (!initial.length && !form.dataset.context) {
    try {
      const retained = JSON.parse(root.sessionStorage.getItem(key) || 'null');
      if (retained?.rows) {
        rows = retained.rows.map(normalize);
        if ([...show.options].some(option => option.value === retained.show)) show.value = retained.show;
        if ([...target.options].some(option => option.value === retained.target)) target.value = retained.target;
      }
    } catch (_) {}
  }
  function field(parent, text, node) {
    const label = document.createElement('label'); label.textContent = text; label.append(node); parent.append(label);
    return label;
  }
  function draw() {
    rowsBox.replaceChildren();
    rows.forEach((row, index) => {
      const group = document.createElement('fieldset'), legend = document.createElement('legend');
      legend.textContent = `${index + 1}. ${row.source_path.split('/').pop()}`; group.append(legend);
      const path = document.createElement('input'); path.type = 'text'; path.value = row.source_path; path.required = true;
      field(group, 'Absolute local path ', path);
      path.addEventListener('input', () => {row.source_path = path.value; save();});
      const episode = document.createElement('select'); episode.required = true;
      [['', 'Choose episode'], ['new', 'Create a new episode'], ...episodes.filter(ep => String(ep.show_id) === show.value)
        .map(ep => [String(ep.id), `Existing: ${ep.title}`])].forEach(([value, title]) => {
        const option = document.createElement('option'); option.value = value; option.textContent = title; episode.append(option);
      });
      episode.value = row.episode_id;
      field(group, 'Episode ', episode);
      const title = document.createElement('input'); title.type = 'text'; title.maxLength = 160; title.value = row.new_title;
      const titleLabel = field(group, 'New episode title ', title);
      const syncEpisode = () => {
        row.episode_id = episode.value; titleLabel.hidden = episode.value !== 'new'; title.required = episode.value === 'new'; save();
      };
      episode.addEventListener('change', syncEpisode); syncEpisode();
      title.addEventListener('input', () => {row.new_title = title.value; save();});
      [['episode_number', 'Episode number (optional) ', 0], ['runtime_seconds', 'Manual runtime seconds (optional) ', 1]].forEach(([name, text, min]) => {
        const input = document.createElement('input'); input.type = 'number'; input.min = String(min); input.step = '1'; input.value = row[name];
        field(group, text, input); input.addEventListener('input', () => {row[name] = input.value; save();});
      });
      const mode = document.createElement('select'); mode.required = true;
      [['', 'Choose encoding state'], [modes[0], 'Needs encoding'], [modes[1], 'Already encoded']].forEach(([value, text]) => {
        const option = document.createElement('option'); option.value = value; option.textContent = text; mode.append(option);
      });
      mode.value = row.encoding_mode; field(group, 'Encoding state ', mode);
      mode.addEventListener('change', () => {row.encoding_mode = mode.value; save();});
      const rowErrors = [row.error, ...(errors[index] || [])].filter(Boolean);
      if (rowErrors.length) {const alert = document.createElement('p'); alert.className = 'errorlist'; alert.setAttribute('role', 'alert'); alert.textContent = rowErrors.join(' '); group.append(alert);}
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove file';
      remove.addEventListener('click', () => {rows.splice(index, 1); draw(); save();}); group.append(remove); rowsBox.append(group);
    });
    save();
  }
  const add = entries => {
    rows = append(rows, entries, form.dataset.episode);
    draw(); status.textContent = `${rows.length} file${rows.length === 1 ? '' : 's'} in this intake.`;
  };
  document.getElementById('add-pasted-path').addEventListener('click', () => {
    if (manual.value) {add([{path: manual.value}]); manual.value = '';}
  });
  document.getElementById('choose-media').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true; status.textContent = 'Opening local chooser…'; save();
    try {
      const response = await fetch(form.dataset.picker, {method: 'POST', headers: {'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value,
        'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({kind: 'files'}), credentials: 'same-origin'});
      const result = await response.json();
      if (result.cancelled) {status.textContent = 'Chooser cancelled; draft retained.'; return;}
      if (!response.ok || !Array.isArray(result.paths)) throw Error(result.error || 'Chooser failed.');
      if (result.error && !result.paths.length) throw Error(result.error);
      add(result.entries || result.paths);
    } catch (error) {status.textContent = `${error.message || 'Chooser unavailable.'} Your draft remains; paste a path to continue.`;}
    finally {button.disabled = false;}
  });
  show.addEventListener('change', () => {rows = changeShow(rows, episodes, show.value); draw();
    status.textContent = 'Show changed. Only incompatible existing episode choices were cleared; titles, paths and metadata remain.';});
  const syncTarget = () => { show.disabled = !target.value; if (!target.value) show.value = ''; save(); };
  target.addEventListener('change', syncTarget);
  syncTarget();
  document.getElementById('apply-mode-button').addEventListener('click', () => {
    const mode = document.getElementById('apply-mode').value;
    if (modes.includes(mode)) {rows.forEach(row => {row.encoding_mode = mode;}); draw();}
  });
  form.addEventListener('submit', () => {if (manual.value) {add([{path: manual.value}]); manual.value = '';} save();});
  document.querySelectorAll('[data-intake-confirm]').forEach(confirm => confirm.addEventListener('submit', save));
  draw();
})(typeof window !== 'undefined' ? window : globalThis);
