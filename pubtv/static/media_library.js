(function () {
  'use strict';
  const order = [], list = document.getElementById('selected-premiere-order'), plan = document.getElementById('plan-selected'), status = document.getElementById('selection-status');
  function drawSelection() {
    if (!list) return;
    list.replaceChildren();
    order.forEach((checkbox, index) => {
      const li = document.createElement('li'); li.textContent = checkbox.dataset.title + ' ';
      [['Earlier', -1], ['Later', 1]].forEach(([text, change]) => {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = text;
        button.disabled = index + change < 0 || index + change >= order.length;
        button.addEventListener('click', () => {const next = index + change; [order[index], order[next]] = [order[next], order[index]]; drawSelection();}); li.append(button);
      });
      list.append(li);
    });
    plan.disabled = !order.length;
  }
  document.querySelectorAll('[data-plan-episode]').forEach(checkbox => checkbox.addEventListener('change', () => {
    if (checkbox.checked) {
      if (order.length && checkbox.dataset.show !== order[0].dataset.show) {
        checkbox.checked = false; status.textContent = 'Select episodes from one show at a time.'; return;
      }
      order.push(checkbox);
    } else {const index = order.indexOf(checkbox); if (index >= 0) order.splice(index, 1);}
    status.textContent = `${order.length} episode(s) selected in the order below.`; drawSelection();
  }));
  if (plan) plan.addEventListener('click', () => {
    if (!order.length) return;
    const query = new URLSearchParams(); order.forEach(checkbox => query.append('episode_ids', checkbox.dataset.planEpisode));
    window.location.assign(order[0].dataset.planner + '?' + query.toString());
  });
  async function post(form, values) {
    const data = new FormData(form); Object.entries(values).forEach(([key, value]) => data.set(key, value));
    const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin'});
    const result = await response.json(); if (!response.ok) throw Error(result.error || 'Relink failed.'); return result;
  }
  document.querySelectorAll('.relink-form').forEach(form => {
    const status = form.querySelector('.relink-status'), actions = form.querySelector('[data-relink-actions]'), input = form.querySelector('[name=path]');
    let generation = 0, polling;
    const cancelPoll = () => {generation++; clearTimeout(polling); actions.replaceChildren();};
    input.addEventListener('input', () => {cancelPoll(); status.textContent = 'Path edited. Start a new review.';});
    function showResult(result, current) {
      if (current !== generation) return;
      actions.replaceChildren();
      if (['queued', 'checking', 'confirm_queued', 'confirming'].includes(result.status)) {
        status.textContent = ['confirm_queued', 'confirming'].includes(result.status) ? 'Confirmation queued. The worker rechecks the complete file before saving its location.' : 'Background identity review pending. The local worker checks the complete file.';
        polling = setTimeout(async () => {
          if (current !== generation) return;
          try {showResult(await post(form, {action: 'status', review_token: result.review_token}), current);}
          catch (error) {status.textContent = error.message + ' Start a new review to continue.';}
        }, 5000);
      } else if (result.status === 'matched') {
        status.textContent = 'Reviewed bytes match this media version. Confirm to update its location; queued work will require fresh review.';
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Confirm matching location';
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {const confirmed = await post(form, {action: 'confirm', review_token: result.review_token});
            showResult(confirmed, current);}
          catch (error) {status.textContent = error.message; button.disabled = false;}
        }); actions.append(button);
      } else if (result.status === 'confirmed') {
        status.textContent = 'Matching location saved after final byte verification. Preparation history retained. Refresh to see new availability.';
      } else if (result.status === 'new_version') {
        status.textContent = result.error;
        const link = document.createElement('a'); link.textContent = 'Review this file as a new version'; link.href = result.new_intake_url; actions.append(link);
      } else {status.textContent = result.error || result.status;}
    }
    form.addEventListener('submit', async event => {
      event.preventDefault(); cancelPoll(); const current = generation;
      status.textContent = 'Starting background identity review…';
      try {showResult(await post(form, {action: 'review'}), current);} catch (error) {status.textContent = error.message;}
    });
    form.querySelector('[data-relink-choose]').addEventListener('click', async event => {
      const button = event.currentTarget; button.disabled = true;
      try {
        const response = await fetch(form.dataset.picker, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value}, body: new URLSearchParams({kind: 'file'}), credentials: 'same-origin'});
        const result = await response.json();
        if (result.cancelled) {status.textContent = 'Chooser cancelled; path retained.'; return;}
        if (!response.ok || !result.path) throw Error(result.error || 'Chooser unavailable. Paste an absolute path.');
        input.value = result.path; cancelPoll(); status.textContent = 'Path selected. Review it before confirming.';
      } catch (error) {status.textContent = error.message;} finally {button.disabled = false;}
    });
  });
  document.querySelectorAll('.queue-batch-remove').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault(); const error = form.querySelector('[data-remove-error]'), button = form.querySelector('button');
    error.hidden = true; button.disabled = true;
    try {
      const response = await fetch(form.action, {method: 'POST', headers: {'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json'}, body: new FormData(form), credentials: 'same-origin'});
      const result = await response.json(); if (!response.ok) throw Error(result.error || 'Batch removal failed.'); form.closest('article').remove();
    } catch (exception) {error.textContent = exception.message; error.hidden = false; button.disabled = false;}
  }));
})();
