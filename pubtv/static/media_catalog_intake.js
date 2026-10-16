(function () {
  'use strict';
  const form = document.getElementById('media-intake-form');
  if (!form) return;
  const radios = [...form.querySelectorAll('[name=purpose]')];
  const fields = [...form.querySelectorAll('[data-purpose-field]')];
  function sync() {
    const purpose = radios.find(radio => radio.checked)?.value || '';
    fields.forEach(field => {
      const role = field.dataset.purposeField;
      const visible = role === purpose || (role === 'episode' && purpose !== 'reusable' && purpose !== '');
      field.hidden = !visible;
      field.querySelectorAll('input, select').forEach(control => {
        control.disabled = !visible;
        control.required = visible && (role === 'episode' || role === 'existing_episode' || (role === 'new_episode' && control.name === 'title'));
      });
    });
  }
  radios.forEach(radio => radio.addEventListener('change', sync));
  sync();
})();
