/* Read durable status; polling never starts or retries work. */
(() => {
  const summary = document.getElementById('media-queue-summary');
  const labels = {queued: 'Queued', pending: 'Queued', encoding: 'Encoding', validating: 'Validating', transferring: 'Transferring', verifying: 'Verifying', ready: 'Ready', blocked: 'Needs attention', failed: 'Needs attention'};
  let inFlight = false;
  async function refresh() {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const response = await fetch('/media/status/', {headers: {Accept: 'application/json'}, cache: 'no-store'});
      if (!response.ok) throw new Error('Queue unavailable');
      const data = await response.json(), counts = data.counts;
      const active = ['encoding', 'validating', 'transferring', 'verifying'].reduce((n, key) => n + (counts[key] || 0), 0);
      if (summary) summary.textContent = `Media: ${counts.queued || 0} queued · ${active} processing · ${counts.ready || 0} ready · ${(counts.blocked || 0) + (counts.failed || 0)} need attention`;
      for (const item of data.items || []) {
        const row = document.getElementById(`item-${item.id}`);
        if (!row) continue;
        const status = row.querySelector('[data-item-status]');
        if (status) status.textContent = labels[item.state] || 'Needs attention';
        const blocker = row.querySelector('[data-item-blocker]');
        if (blocker) blocker.textContent = item.blocker || '';
        const progress = row.querySelector('progress');
        if (progress) progress.hidden = !['encoding', 'validating', 'transferring', 'verifying'].includes(item.state);
        const retry = row.querySelector('[data-item-retry]');
        if (retry) retry.hidden = !['blocked', 'failed'].includes(item.state);
      }
      for (const publication of data.publications || []) {
        const card = document.getElementById(`publication-${publication.id}`);
        if (!card) continue;
        const label = card.querySelector('[data-publication-readiness]');
        if (label) label.textContent = publication.label;
        for (const action of ['generate_nmg', 'generate_bin']) {
          const input = card.querySelector(`input[name="action"][value="${action}"]`);
          if (input) input.form.querySelector('button').disabled = !publication.ready;
        }
      }
    } catch (_) {
      if (summary) summary.textContent = 'Media queue status unavailable — open queue';
    } finally { inFlight = false; }
  }
  refresh();
  setInterval(refresh, 5000);
  document.addEventListener('visibilitychange', refresh);
})();
