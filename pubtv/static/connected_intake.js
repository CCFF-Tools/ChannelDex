/* Connected intake chooser and editable-row behavior. */
(function (root) {
  'use strict';

  function splitPaths(value) {
    return String(value || '').split(/\r?\n/).map(function (item) { return item.trim(); }).filter(Boolean);
  }

  function mergePaths(current, selected) {
    var result = splitPaths(current);
    (selected || []).forEach(function (path) {
      path = String(path || '').trim();
      if (path && result.indexOf(path) === -1) result.push(path);
    });
    return result;
  }

  function stateFor(payload) {
    payload = payload || {};
    return {step: payload.step || 'select', rows: Array.isArray(payload.rows) ? payload.rows : []};
  }

  function visibleEpisodeIds(options, showId) {
    return (options || []).filter(function (option) {
      return !option.showId || String(option.showId) === String(showId || '');
    }).map(function (option) { return String(option.id); });
  }

  var api = {splitPaths: splitPaths, mergePaths: mergePaths, stateFor: stateFor,
             visibleEpisodeIds: visibleEpisodeIds};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (!root.document) return;

  root.document.querySelectorAll('textarea[name="paths"]').forEach(function (area) {
    area.addEventListener('change', function () { area.value = splitPaths(area.value).join('\n'); });
  });

  var form = root.document.getElementById('connected-intake-form');
  var chooser = form && form.querySelector('[data-choose-files]');
  var area = form && form.querySelector('textarea[name="paths"]');
  var status = form && form.querySelector('[data-picker-status]');
  if (chooser && area) chooser.addEventListener('click', async function () {
    status.textContent = 'Opening Finder…';
    chooser.disabled = true;
    try {
      var csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
      var originField = form.querySelector('[name=originating_station]');
      var body = new URLSearchParams({kind: 'files'});
      if (originField) body.append('originating_station', originField.value);
      var response = await root.fetch(form.dataset.picker, {
        method: 'POST', headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded'}, body: body
      });
      var result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The local file chooser failed.');
      if (result.cancelled) status.textContent = 'File selection cancelled.';
      else if (result.paths && result.paths.length) {
        area.value = mergePaths(area.value, result.paths).join('\n');
        status.textContent = result.paths.length + ' file' + (result.paths.length === 1 ? '' : 's') + ' selected.';
      } else status.textContent = result.error || 'No files selected.';
    } catch (error) {
      status.textContent = error.message || 'The local file chooser is unavailable; paste absolute paths below.';
    } finally { chooser.disabled = false; }
  });

  root.document.querySelectorAll('[data-file-row]').forEach(function (row) {
    var kind = row.querySelector('[data-kind-select]');
    var episodeFields = row.querySelector('[data-episode-fields]');
    var reusableFields = row.querySelector('[data-reusable-fields]');
    var show = row.querySelector('[data-show-select]');
    var episode = row.querySelector('[data-episode-select]');
    function syncKind() {
      var reusable = kind && kind.value === 'reusable';
      if (episodeFields) episodeFields.hidden = reusable;
      if (reusableFields) reusableFields.hidden = !reusable;
    }
    function syncEpisodes() {
      if (!show || !episode) return;
      Array.from(episode.options).forEach(function (option) {
        option.hidden = Boolean(option.dataset.show) && option.dataset.show !== show.value;
      });
      var selected = episode.options[episode.selectedIndex];
      if (selected && selected.hidden) episode.value = '';
    }
    if (kind) kind.addEventListener('change', syncKind);
    if (show) show.addEventListener('change', syncEpisodes);
    syncKind(); syncEpisodes();
  });
})(typeof window !== 'undefined' ? window : globalThis);
