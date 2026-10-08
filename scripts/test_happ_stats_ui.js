const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  return {
    children: [],
    textContent: '',
    style: {},
    dataset: {},
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
  const liveRoot = element();
  const canvas = element();
  const legend = element();
  const activityRows = element();
  let chart;
  let refresh;
  let payload;
  let httpStatus = 200;
  let historyStatus = 200;
  let changeRange;
  let deferHistory = false;
  let finishHistory;
  let heldSignal;
  const ranges = [];
  const range = { value: '10', closest(selector) { return selector === '[data-happ-chart-range]' ? this : null; } };
  class FakeChart {
    constructor(target, config) {
      assert.equal(target, canvas);
      this.data = config.data;
      this.options = config.options;
      chart = this;
    }
    update() {}
    destroy() { this.destroyed = true; }
  }
  const received = element();
  const sent = element();
  const account = { dataset: { happAccount: 'personal-a' }, querySelector: (selector) => selector === '[data-account-download]' ? received : sent };
  const metrics = new Map();
  const name = '<script>display-name</script>';
  const historyAt = Date.parse('2026-10-08T00:01:13+00:00');
  let historyUsers = [
    { user_key: 'personal-a', user_name: name, peak_bytes_per_second: null, points: [{ x: historyAt, y: null }] },
    { user_key: 'personal-b', user_name: 'Bob', peak_bytes_per_second: null, points: [{ x: historyAt, y: null }] },
  ];
  let historyCount = 2;
  const document = {
    hidden: false,
    addEventListener(type, callback) { if (type === 'change') changeRange = callback; },
    createElement: element,
    querySelectorAll: (selector) => selector === '[data-happ-account]' ? [account] : [],
    querySelector(selector) {
      if (selector === '[data-happ-live]') return liveRoot;
      if (selector === '[data-happ-traffic-chart]') return canvas;
      if (selector === '[data-happ-chart-legend]') return legend;
      if (selector === '[data-happ-activity-users]') return activityRows;
      if (['[data-happ-activity-status]', '[data-happ-users-online]', '[data-happ-current]', '[data-happ-lifetime-download]', '[data-happ-lifetime-upload]', '[data-happ-peak-label]'].includes(selector)) {
        if (!metrics.has(selector)) metrics.set(selector, element());
        return metrics.get(selector);
      }
      if (selector === '[data-happ-chart-range]') return range;
      if (selector === '[data-happ-connections]') return connections;
      if (selector === '[data-happ-user-traffic]') return users;
      if (selector === '[data-happ-top-users]') return topUsers;
      if (['[data-happ-online-count]', '[data-happ-online]', '[data-happ-download]', '[data-happ-upload]', '[data-happ-speed]', '[data-happ-chart-count]', '[data-happ-chart-overflow]', '[data-happ-chart-status]'].includes(selector)) {
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
      Chart: FakeChart,
      location: { pathname: '/happ-server', href: 'http://localhost/happ-server', origin: 'http://localhost', reload() {} },
      addEventListener() {},
      setInterval(callback) { refresh = callback; return 1; },
      clearInterval() {},
      setTimeout() { return 1; },
      clearTimeout() {},
    },
    fetch: async (url, options) => {
      assert.ok(options.signal);
      if (url.startsWith('/happ-server/traffic?minutes=')) {
        const minutes = Number(new URL(url, 'http://localhost').searchParams.get('minutes'));
        ranges.push(minutes);
        const result = { minutes, direction: new URL(url, 'http://localhost').searchParams.get('direction'), since: historyAt - minutes * 60000, until: historyAt, sampled_at: historyAt, user_count: historyCount, users: historyUsers };
        const response = { status: historyStatus, ok: historyStatus === 200, json: async () => result };
        if (deferHistory) {
          deferHistory = false;
          heldSignal = options.signal;
          return new Promise((resolve) => { finishHistory = () => resolve(response); });
        }
        return response;
      }
      assert.match(url, /^\/happ-server\/live\?seconds=\d+$/);
      return {
        status: httpStatus, ok: httpStatus === 200,
        json: async () => payload || ({
          sampled_at: '2026-10-08T00:00:00+00:00',
          traffic_samples: [{ id: 'connection-a', user_key: 'personal-a', download_bytes: 3072 }, { id: 'connection-b', user_key: 'personal-b', download_bytes: 1024 }],
          online_count: 1, download: '3.0 KB', upload: '192 B',
          connections: [{ user_name: name, ip: '203.0.113.1', duration: '4 мин. 30 сек.', download: '3.0 KB', upload: '192 B', network: 'TCP', destination: 'latest.example:443' }],
          users: [{ user_key: 'personal-a', user_name: name, connections: 1, download: '3.0 KB', upload: '192 B' }, { user_key: 'personal-b', user_name: 'Bob', connections: 1, download_bytes: 1024 }],
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
  assert.equal(chart.data.datasets.length, 2);
  assert.equal(chart.data.datasets[0].data[0].y, null);
  assert.equal(legend.children[0].children[1].textContent, name);
  assert.equal(chart.options.animation.duration, 900);
  const colors = chart.data.datasets.map((series) => series.borderColor);
  payload = {
    sampled_at: '2026-10-08T00:00:02+00:00',
    users: [{ user_key: 'personal-b', user_name: 'Bob', connections: 1 }, { user_key: 'personal-a', user_name: name, connections: 2 }],
    traffic_samples: [{ id: 'connection-a', user_key: 'personal-a', download_bytes: 3072 + 2097152 }, { id: 'connection-b', user_key: 'personal-b', download_bytes: 1024 + 1048576 }, { id: 'new-connection', user_key: 'personal-a', download_bytes: 90000000 }],
  };
  await refresh();
  historyUsers = [
    { user_key: 'personal-a', user_name: name, peak_bytes_per_second: 1048576, points: [{ x: historyAt - 2000, y: 1 }, { x: historyAt, y: 0 }] },
    { user_key: 'personal-b', user_name: 'Bob', peak_bytes_per_second: 524288, points: [{ x: historyAt - 2000, y: .5 }, { x: historyAt, y: 0 }] },
  ];
  range.value = '30';
  await changeRange({ target: range });
  assert.equal(chart.data.datasets[0].data[0].y, 1);
  assert.equal(chart.data.datasets[1].data[0].y, .5);
  assert.deepEqual(chart.data.datasets.map((series) => series.borderColor), colors);
  assert.equal(metrics.get('[data-happ-speed]').textContent, '1.50 МБ/с');
  assert.equal(legend.children[0].children[2].textContent, 'Пик: 1.00 МБ/с');
  assert.equal(legend.children[1].children[2].textContent, 'Пик: 0.50 МБ/с');
  assert.equal(chart.options.scales.y.max, 1.1);
  payload.sampled_at = '2026-10-08T00:00:03+00:00';
  payload.traffic_samples = [{ id: 'connection-a', user_key: 'personal-a', download_bytes: 0 }];
  await refresh();
  assert.equal(chart.data.datasets[0].data.at(-1).y, 0);
  assert.equal(chart.data.datasets[1].data.at(-1).y, 0);
  assert.equal(legend.children[0].children[2].textContent, 'Пик: 1.00 МБ/с');
  assert.equal(legend.children[0].children[2].title, 'Максимум за 30 минут');
  assert.equal(chart.options.scales.y.max, 1.1);
  const length = chart.data.datasets[0].data.length;
  await refresh();
  assert.equal(chart.data.datasets[0].data.length, length);
  for (const minutes of [10, 30, 60, 90]) {
    range.value = String(minutes);
    await changeRange({ target: range });
    assert.equal(chart.options.scales.x.max - chart.options.scales.x.min, minutes * 60000);
    assert.equal(legend.children[0].children[2].textContent, 'Пик: 1.00 МБ/с');
  }
  deferHistory = true;
  range.value = '30';
  const older = changeRange({ target: range });
  assert.equal(chart.data.datasets.length, 0);
  assert.equal(legend.children.length, 0);
  assert.equal(metrics.get('[data-happ-chart-count]').textContent, '0 / 10');
  range.value = '90';
  await changeRange({ target: range });
  assert.equal(heldSignal.aborted, true);
  finishHistory();
  await older;
  assert.equal(chart.options.scales.x.max - chart.options.scales.x.min, 90 * 60000);
  historyUsers = [{ user_key: 'new-user', user_name: 'New', peak_bytes_per_second: 2097152, points: [{ x: historyAt, y: 2 }] }, ...historyUsers.slice().reverse()];
  await changeRange({ target: range });
  assert.equal(chart.data.datasets[1].borderColor, colors[1]);
  assert.equal(chart.data.datasets[2].borderColor, colors[0]);
  assert.equal(new Set(chart.data.datasets.map((series) => series.borderColor)).size, 3);
  assert.ok(chart.data.datasets.every((series) => series.cubicInterpolationMode === 'monotone'));
  historyUsers = Array.from({ length: 12 }, (_, index) => ({ user_key: `user-${index}`, user_name: `User ${index}`, peak_bytes_per_second: 1048576, points: [{ x: historyAt, y: 1 }] }));
  historyCount = 12;
  await changeRange({ target: range });
  assert.equal(chart.data.datasets.length, 10);
  assert.equal(new Set(chart.data.datasets.map((series) => series.borderColor)).size, 10);
  assert.equal(legend.children.length, 10);
  assert.equal(metrics.get('[data-happ-chart-overflow]').textContent, 'Ещё 2 пользователей в истории');
  httpStatus = 503;
  await refresh();
  assert.equal(metrics.get('[data-happ-chart-status]').textContent, 'Нет свежих данных');
  httpStatus = 200;
  historyStatus = 503;
  await changeRange({ target: range });
  assert.equal(metrics.get('[data-happ-chart-status]').textContent, 'История недоступна');
  historyStatus = 200;
  historyUsers = [];
  historyCount = 0;
  await changeRange({ target: range });
  assert.equal(chart.data.datasets.length, 0);
  assert.equal(legend.children[0].textContent, 'В выбранном отрезке ещё нет замеров скорости.');
  historyUsers = [{ user_key: 'personal-a', user_name: name, peak_bytes_per_second: 2097152, points: [{ x: historyAt, y: 2 }] }];
  historyCount = 1;
  await changeRange({ target: range });
  assert.equal(legend.children[0].children[2].textContent, 'Пик: 2.00 МБ/с');
  assert.equal(chart.options.scales.y.max, 2.2);
  payload = { sampled_at: '2026-10-08T00:00:04+00:00', users: [{ user_key: 'personal-a', connections: 1 }], traffic_samples: [{ id: 'reconnected', user_key: 'personal-a', download_bytes: 90000000 }] };
  await refresh();
  assert.equal(metrics.get('[data-happ-speed]').textContent, '0.00 МБ/с');
  payload.sampled_at = '2026-10-08T00:00:05+00:00';
  payload.traffic_samples[0].download_bytes += 2097152;
  await refresh();
  assert.equal(metrics.get('[data-happ-speed]').textContent, '2.00 МБ/с');
  assert.ok([10, 30, 60, 90].every((minutes) => ranges.includes(minutes)));
  console.log('PASS: HAPP live rates, persisted maxima, four ranges, request races, colors, limit and stale/empty states');
  payload.activity = { seconds: 5, fresh: true, sampled_at: historyAt, users: [
    { user_key: 'personal-a', user_name: name, status: 'active', connections: 3, ips: ['203.0.113.1', '2001:db8::1'], download_bytes: 1073741824, upload_bytes: 1048576, download_rate: 2097152, upload_rate: 1048576, download_peak: 4194304, upload_peak: 2097152 },
    { user_key: 'offline', user_name: 'Closed', status: 'offline', connections: 0, ips: [], download_bytes: 1073741824, upload_bytes: 1048576, download_rate: 0, upload_rate: 0, download_peak: null, upload_peak: null },
  ] };
  await refresh();
  assert.equal(activityRows.children.length, 2);
  const cells = activityRows.children[0].children;
  assert.equal(cells[0].children[0].textContent, name);
  assert.equal(cells[0].children[1].textContent, 'Активен');
  assert.equal(cells[1].textContent, '203.0.113.1, 2001:db8::1');
  assert.equal(cells[2].textContent, '3');
  assert.equal(cells[3].textContent, '1.00 ГБ / 1.00 МБ');
  assert.equal(cells[4].textContent, '2.00 / 1.00');
  assert.equal(cells[5].textContent, '4.00 / 2.00');
  assert.equal(metrics.get('[data-happ-lifetime-download]').textContent, '2.00 ГБ');
  assert.equal(metrics.get('[data-happ-users-online]').textContent, '1 / 1');
  const secondsControl = { value: '60', closest(selector) { return selector === '[data-happ-peak-seconds]' ? this : null; } };
  await changeRange({ target: secondsControl });
  await refresh();
  assert.equal(metrics.get('[data-happ-users-online]').textContent, '— / —');
  payload.activity.seconds = 60;
  payload.activity.users[0].status = 'connected';
  await refresh();
  assert.equal(activityRows.children[0].children[0].children[1].textContent, 'Подключён · без трафика');
  assert.equal(metrics.get('[data-happ-peak-label]').textContent, 'за 60 сек, МБ/с');
  secondsControl.value = '61';
  await changeRange({ target: secondsControl });
  assert.equal(secondsControl.value, '60');
  const directionControl = { value: 'upload', closest(selector) { return selector === '[data-happ-chart-direction]' ? this : null; } };
  await changeRange({ target: directionControl });
  assert.equal(chart.data.datasets.length, 1);
  assert.ok(canvas.ariaLabel.includes('отправки'));
  httpStatus = 503;
  await refresh();
  assert.equal(metrics.get('[data-happ-current]').textContent, '— / —');
  assert.equal(activityRows.children[0].children[0].textContent, 'Ожидание свежих данных.');
  console.log('PASS: activity identity, IPs, lifetime totals, rates, peak window, upload mode, races and stale states');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});