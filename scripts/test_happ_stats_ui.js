const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  return {
    children: [],
    textContent: '',
    style: {},
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
  };
}

function testUserCreationDialog() {
  const clickHandlers = [];
  const keyHandlers = [];
  const name = { value: 'draft', focus() { this.focused = true; } };
  const expiry = { value: '2030-01-01T12:00' };
  const form = { reset() { name.value = ''; expiry.value = ''; } };
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
    closest() { return null; },
    getBoundingClientRect() { return { left: 100, top: 100, right: 500, bottom: 400 }; },
    querySelector(selector) { return selector === 'form' ? form : selector === '[name="name"]' ? name : null; },
  };
  const sandbox = {
    document: {
      addEventListener(type, handler) {
        if (type === 'click') clickHandlers.push(handler);
        if (type === 'keydown') keyHandlers.push(handler);
      },
      querySelector(selector) { return selector === '[data-happ-user-create-dialog]' ? dialog : null; },
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'server', 'panel', 'static', 'happ-actions.js'), 'utf8'), sandbox);
  function click(selector) {
    for (const handler of clickHandlers) {
      handler({ target: { closest(candidate) { return candidate === selector ? {} : null; } }, preventDefault() {} });
    }
  }
  click('[data-happ-user-create-open]');
  assert.equal(dialog.open, true);
  assert.equal(name.value, '');
  assert.equal(expiry.value, '');
  assert.equal(name.focused, true);
  click('[data-happ-user-create-cancel]');
  assert.equal(dialog.open, false);
  click('[data-happ-user-create-open]');
  for (const handler of clickHandlers) handler({ target: dialog, clientX: 200, clientY: 200 });
  assert.equal(dialog.open, true);
  for (const handler of clickHandlers) handler({ target: dialog, clientX: 20, clientY: 20 });
  assert.equal(dialog.open, false);
  click('[data-happ-user-create-open]');
  for (const handler of keyHandlers) handler({ key: 'Escape' });
  assert.equal(dialog.open, false);
  sandbox.document.querySelector = () => null;
  click('[data-happ-user-create-open]');
  console.log('PASS: HAPP user creation dialog opens, focuses, resets and cancels safely');
}

async function main() {
  testUserCreationDialog();
  const connections = element();
  const users = element();
  const topUsers = element();
  const received = element();
  const sent = element();
  const account = { dataset: { happAccount: 'personal-a' }, querySelector: (selector) => selector === '[data-account-download]' ? received : sent };
  const metrics = new Map();
  const name = '<script>display-name</script>';
  const document = {
    hidden: false,
    addEventListener() {},
    createElement: element,
    querySelectorAll: (selector) => selector === '[data-happ-account]' ? [account] : [],
    querySelector(selector) {
      if (selector === '[data-happ-live]') return {};
      if (selector === '[data-happ-connections]') return connections;
      if (selector === '[data-happ-user-traffic]') return users;
      if (selector === '[data-happ-top-users]') return topUsers;
      if (['[data-happ-online-count]', '[data-happ-online]', '[data-happ-download]', '[data-happ-upload]'].includes(selector)) {
        if (!metrics.has(selector)) metrics.set(selector, element());
        return metrics.get(selector);
      }
      return null;
    },
  };
  const sandbox = {
    AbortController,
    URL,
    console,
    document,
    window: {
      location: { pathname: '/happ-server', href: 'http://localhost/happ-server', origin: 'http://localhost', reload() {} },
      addEventListener() {},
      setInterval() { return 1; },
      clearInterval() {},
      setTimeout() { return 1; },
      clearTimeout() {},
    },
    fetch: async (url, options) => {
      assert.equal(url, '/happ-server/live');
      assert.ok(options.signal);
      return {
        status: 200, ok: true,
        json: async () => ({
          online_count: 1, download: '3.0 KB', upload: '192 B',
          connections: [{ user_name: name, ip: '203.0.113.1', duration: '4 мин. 30 сек.', download: '3.0 KB', upload: '192 B', network: 'TCP', destination: 'latest.example:443' }],
          users: [{ user_name: name, connections: 1, download: '3.0 KB', upload: '192 B' }],
          top_users: [
            { user_name: name, connections: 3, download: '3.0 KB', upload: '192 B', download_bytes: 3072, upload_bytes: 192 },
            { user_name: 'Bob', connections: 1, download: '1.0 KB', upload: '512 B', download_bytes: 1024, upload_bytes: 512 },
          ],
          account_traffic: { 'personal-a': { download: '64.0 MB', upload: '8.0 MB' } },
        }),
      };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'server', 'panel', 'static', 'panel.js'), 'utf8'), sandbox);
  await new Promise(setImmediate);
  assert.equal(connections.children.length, 1);
  assert.deepEqual(connections.children[0].children.map((cell) => cell.textContent), [name, '203.0.113.1', '4 мин. 30 сек.', '3.0 KB', '192 B', 'TCP', 'latest.example:443']);
  assert.equal(topUsers.children.length, 2);
  assert.equal(topUsers.children[0].children[0].children[1].textContent, name);
  assert.deepEqual(topUsers.children[0].children[1].children.map((measure) => measure.children[0].textContent), ['Скачано', 'Отправлено']);
  assert.equal(topUsers.children[0].children[1].children[0].children[1].children[0].style.width, '100%');
  assert.equal(topUsers.children[1].children[1].children[0].children[1].children[0].style.width, '33.33333333333333%');
  assert.deepEqual(users.children[0].children.map((cell) => cell.textContent), [name, '1', '3.0 KB', '192 B']);
  assert.equal(metrics.get('[data-happ-download]').textContent, '3.0 KB');
  assert.equal(received.textContent, '64.0 MB');
  assert.equal(sent.textContent, '8.0 MB');
  console.log('PASS: HAPP TOP-5 bars and IP/protocol aggregate rows render safely with live user totals');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});