const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function classList() {
  const values = new Set();
  return {
    values,
    add: (value) => values.add(value),
    remove: (value) => values.delete(value),
    toggle: (value, enabled) => enabled ? values.add(value) : values.delete(value),
  };
}

async function main() {
  const summary = { dataset: { checkTags: '["auto-8"]', checkPrefix: '' }, classList: classList(), textContent: 'Запущена в фоне' };
  const button = { disabled: true };
  const checkedAt = { textContent: '' };
  const row = { classList: classList(), querySelector: (selector) => selector.includes('button') ? button : selector === '[data-outbound-checked-at]' ? checkedAt : { textContent: '' } };
  const documentEvents = new Map();
  const clickHandlers = [];
  const editTag = { value: '' };
  const editButton = { disabled: false, textContent: 'Сохранить' };
  const editJson = {
    value: '',
    setCustomValidity(value) { this.validationMessage = value; },
    reportValidity() { this.reported = true; },
    focus() { this.focused = true; },
  };
  const editDialog = {
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelector(selector) {
      if (selector === '[data-outbound-edit-tag]') return editTag;
      if (selector === '[data-outbound-edit-json]') return editJson;
      return editButton;
    },
  };
  const dialogTitle = { textContent: '' };
  const dialogMessage = { textContent: '' };
  const gatewayDialog = {
    showModal() { this.open = true; },
    querySelector(selector) {
      if (selector === '[data-gateway-dialog-title]') return dialogTitle;
      if (selector === '[data-gateway-dialog-message]') return dialogMessage;
      return { focus() {} };
    },
  };
  const cell = { dataset: { outboundCheckTag: 'auto-8' }, textContent: 'Проверяется', closest: () => row };
  const timeouts = [];
  const faviconLinks = [{ href: '/favicon.svg' }, { href: '/favicon.png' }];
  let faviconFrames = 0;
  let renderFaviconFrame;
  let refresh;
  let requests = 0;
  let phase = 'hang';
  const sandbox = {
    AbortController,
    URL,
    console,
    document: {
      hidden: false,
      addEventListener(name, callback) {
        documentEvents.set(name, callback);
        if (name === 'click') clickHandlers.push(callback);
      },
      createElement(name) {
        assert.equal(name, 'canvas');
        const context = new Proxy({}, { get: () => () => {} });
        return {
          getContext: () => context,
          toDataURL: () => `data:image/png;frame=${++faviconFrames}`,
        };
      },
      querySelector(selector) {
        if (selector === '[data-outbound-summary]') return summary;
        if (selector.startsWith('[data-outbound-checks]')) return {};
        if (selector === '[data-gateway-dialog]') return gatewayDialog;
        if (selector === '[data-outbound-edit-dialog]') return editDialog;
        return null;
      },
      querySelectorAll: (selector) => selector === 'link[rel~="icon"]' ? faviconLinks : selector === '[data-outbound-check-tag]' ? [cell] : [],
    },
    window: {
      location: { pathname: '/outbounds', href: 'http://localhost/outbounds', origin: 'http://localhost', reload() {} },
      addEventListener() {},
      clearInterval() {},
      setInterval(callback, delay) {
        if (delay === 120) renderFaviconFrame = callback;
        else refresh = callback;
        return 1;
      },
      setTimeout(callback, delay) { timeouts.push({ callback, delay }); return timeouts.length; },
      clearTimeout() {},
    },
    fetch(url, options) {
      requests += 1;
      assert.equal(url, '/outbounds/checks');
      if (phase === 'hang') {
        return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
      }
      if (phase === 'unavailable') return Promise.resolve({ status: 503, ok: false });
      return Promise.resolve({
        status: 200,
        ok: true,
        json: async () => ({ checks: [{ tag: 'auto-8', state: 'error', message: 'Нет HTTPS-ответа', checked_at: '2026-10-02T21:05:23.914301+00:00' }] }),
      });
    },
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'server', 'panel', 'static', 'panel.js'), 'utf8');
  vm.runInNewContext(source, sandbox);
  const firstFaviconFrame = faviconLinks[0].href;
  renderFaviconFrame();
  assert.notEqual(faviconLinks[0].href, firstFaviconFrame);
  assert.equal(faviconLinks[0].href, faviconLinks[1].href);
  assert.equal(faviconLinks[0].type, 'image/png');
  console.log('PASS: favicon canvas renders and updates both browser icon links');
  assert.equal(requests, 1);
  assert.equal(timeouts[0].delay, 8000);
  timeouts[0].callback();
  await new Promise(setImmediate);
  assert.match(summary.textContent, /Не удалось обновить/);
  phase = 'unavailable';
  await refresh();
  assert.equal(requests, 2);
  assert.match(summary.textContent, /Не удалось обновить/);
  phase = 'complete';
  await refresh();
  assert.equal(requests, 3);
  assert.equal(summary.textContent, 'auto-8: Нет HTTPS-ответа');
  assert.equal(cell.textContent, 'Нет HTTPS-ответа');
  assert.equal(button.disabled, false);
  assert.equal(checkedAt.textContent, '02.10.2026 21:05:23');
  assert.equal(row.classList.values.has('outbound-failed'), true);
  assert.equal(summary.classList.values.has('error'), true);
  console.log('PASS: timeout recovers polling, HTTP errors surface, completed check replaces stale banner');

  const gatewayForm = { dataset: { gatewayMode: 'default' } };
  documentEvents.get('submit')({
    target: {
      matches: () => false,
      closest: (selector) => selector === 'form[data-gateway-mode]' ? gatewayForm : null,
    },
    preventDefault() {},
  });
  assert.equal(gatewayDialog.open, true);
  assert.equal(dialogTitle.textContent, 'Переключить на шлюз по умолчанию?');
  assert.match(dialogMessage.textContent, /основной шлюз сервера/);
  console.log('PASS: default gateway confirmation explains direct server-default egress');

  const profile = { type: 'vless', tag: 'auto-1', server: 'vpn.example.com', tls: { enabled: true } };
  const opener = { dataset: { outboundEdit: JSON.stringify(profile) } };
  for (const callback of clickHandlers) {
    callback({ target: { closest: (selector) => selector === '[data-outbound-edit]' ? opener : null } });
  }
  assert.equal(editDialog.open, true);
  assert.equal(editTag.value, 'auto-1');
  assert.deepEqual(JSON.parse(editJson.value), profile);
  assert.equal(editJson.focused, true);
  for (const callback of clickHandlers) {
    callback({ target: { closest: (selector) => selector === '[data-outbound-edit-cancel]' ? {} : null } });
  }
  assert.equal(editDialog.open, false);
  let prevented = false;
  const editForm = {
    matches(selector) { return selector === '[data-outbound-edit-form]' || selector.includes('/outbounds/import'); },
    querySelector: (selector) => selector === '[data-outbound-edit-json]' ? editJson : editButton,
  };
  editJson.value = 'invalid JSON';
  documentEvents.get('submit')({ target: editForm, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(editJson.reported, true);
  editJson.value = JSON.stringify(profile);
  prevented = false;
  documentEvents.get('submit')({ target: editForm, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(editButton.disabled, true);
  assert.equal(editButton.textContent, 'Сохраняется…');
  console.log('PASS: outbound editor opens existing JSON, cancels and validates before replace-submit');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});