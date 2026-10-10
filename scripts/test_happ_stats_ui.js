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

function testHistoryResetDialog() {
  const clickHandlers = [];
  const cancel = { focused: false, focus() { this.focused = true; } };
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
    closest() { return null; },
    getBoundingClientRect() { return { left: 100, top: 100, right: 500, bottom: 400 }; },
    querySelector(selector) { return selector === '[data-happ-history-reset-cancel]' ? cancel : null; },
  };
  const sandbox = {
    document: {
      addEventListener(type, handler) { if (type === 'click') clickHandlers.push(handler); },
      querySelector(selector) { return selector === '[data-happ-history-reset-dialog]' ? dialog : null; },
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'server', 'panel', 'static', 'happ-actions.js'), 'utf8'), sandbox);
  function click(selector) {
    let prevented = false;
    for (const handler of clickHandlers) {
      handler({ target: { closest(candidate) { return candidate === selector ? {} : null; } }, preventDefault() { prevented = true; } });
    }
    return prevented;
  }
  assert.equal(click('[data-happ-history-reset-open]'), true);
  assert.equal(dialog.open, true);
  assert.equal(cancel.focused, true);
  assert.equal(click('[data-happ-history-reset-cancel]'), true);
  assert.equal(dialog.open, false);
  click('[data-happ-history-reset-open]');
  for (const handler of clickHandlers) handler({ target: dialog, clientX: 200, clientY: 200 });
  assert.equal(dialog.open, true);
  for (const handler of clickHandlers) handler({ target: dialog, clientX: 20, clientY: 20 });
  assert.equal(dialog.open, false);
  sandbox.document.querySelector = () => null;
  click('[data-happ-history-reset-open]');
  console.log('PASS: full history reset dialog opens focused on Cancel, closes by Cancel/backdrop and is inert without the dialog');
}

function createMotionHarness(options = {}) {
  const canvas = element();
  const legend = element();
  const liveRoot = element();
  const metrics = new Map();
  const state = { clock: Date.parse('2026-10-10T01:00:00+00:00'), history: { sampledAt: null, users: [] }, live: null, updates: [], chart: null, refresh: null, ticker: null, change: null, intervals: [], trafficRequests: [], hidden: false };
  class FakeChart {
    constructor(target, config) {
      assert.equal(target, canvas);
      this.config = config;
      this.data = config.data;
      this.options = config.options;
      state.chart = this;
    }
    update(mode) { state.updates.push(mode); }
    destroy() { this.destroyed = true; }
  }
  const document = {
    get hidden() { return state.hidden; },
    addEventListener(type, callback) { if (type === 'change') state.change = callback; },
    createElement: element,
    querySelectorAll: () => [],
    querySelector(selector) {
      if (selector === '[data-happ-live]') return liveRoot;
      if (selector === '[data-happ-traffic-chart]') return canvas;
      if (selector === '[data-happ-chart-legend]') return legend;
      if (['[data-happ-chart-count]', '[data-happ-chart-overflow]', '[data-happ-chart-status]'].includes(selector)) {
        if (!metrics.has(selector)) metrics.set(selector, element());
        return metrics.get(selector);
      }
      return null;
    },
  };
  const sandbox = {
    AbortController,
    Date: class extends Date { static now() { return state.clock; } },
    URL,
    console,
    document,
    window: {
      Chart: FakeChart,
      matchMedia: options.reducedMotion ? () => ({ matches: true }) : undefined,
      location: { pathname: '/happ-server', href: 'http://localhost/happ-server', origin: 'http://localhost', reload() {} },
      addEventListener() {},
      setInterval(callback, delay) {
        state.intervals.push(delay);
        if (delay === 1000) state.refresh = callback; else state.ticker = callback;
        return delay;
      },
      clearInterval() {},
      setTimeout() { return 1; },
      clearTimeout() {},
    },
    fetch: async (url, requestOptions) => {
      assert.ok(requestOptions.signal);
      if (url.startsWith('/happ-server/traffic?')) {
        const params = new URL(url, 'http://localhost').searchParams;
        const minutes = Number(params.get('minutes'));
        state.trafficRequests.push({ minutes, direction: params.get('direction') });
        const direction = params.get('direction');
        const users = direction === 'upload' ? state.history.users.map((user) => ({ ...user, peak_bytes_per_second: user.peak_bytes_per_second === null ? null : user.peak_bytes_per_second / 2, points: user.points.map((point) => ({ ...point, y: point.y === null ? null : point.y / 2 })) })) : state.history.users;
        const result = { minutes, direction, since: state.clock - minutes * 60000, until: state.clock, sampled_at: state.history.sampledAt, user_count: state.history.users.length, users };
        return { status: 200, ok: true, json: async () => result };
      }
      assert.match(url, /^\/happ-server\/live\?seconds=\d+$/);
      return { status: 200, ok: true, json: async () => ({ sampled_at: new Date(state.clock).toISOString(), online_count: 0, users: [], connections: [], traffic_samples: [], top_users: [], activity: state.live }) };
    },
  };
  return {
    state,
    legend,
    async start() {
      vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'server', 'panel', 'static', 'panel.js'), 'utf8'), sandbox);
      await new Promise(setImmediate);
    },
  };
}

async function testHappChartMotion() {
  const T0 = Date.parse('2026-10-10T01:00:00+00:00');
  const MB = 1048576;
  const harness = createMotionHarness();
  const { state, legend } = harness;
  state.clock = T0;
  state.history = {
    sampledAt: T0,
    users: [
      { user_key: 'A', user_name: 'Alice', peak_bytes_per_second: 4 * MB, points: [{ x: T0 - 120000, y: 4 }, { x: T0 - 60000, y: 1 }, { x: T0, y: 2 }] },
      { user_key: 'B', user_name: 'Bob', peak_bytes_per_second: 6 * MB, points: [{ x: T0 - 500000, y: 6 }, { x: T0 - 30000, y: 3 }] },
    ],
  };
  await harness.start();
  const chart = state.chart;
  const series = (key, direction = 'download') => chart.data.datasets.find((dataset) => dataset.userKey === key && dataset.direction === direction);
  const order = () => legend.children.map((item) => item.dataset.happUser);
  const label = (key, direction = 'download') => legend.children.find((item) => item.dataset.happUser === key).children[direction === 'download' ? 2 : 4].textContent;
  const plain = (points) => points.map((point) => [point.x, point.y]);

  assert.equal(chart.options.animation, false);
  assert.equal(chart.options.scales.y.position, 'right');
  assert.equal(chart.options.scales.y.ticks.callback(2), '2.0 МБ/с');
  assert.equal(chart.options.scales.x.max, T0);
  assert.equal(chart.options.scales.x.min, T0 - 600000);
  assert.deepEqual(state.intervals.slice().sort((first, second) => first - second), [100, 1000]);
  assert.equal(typeof state.ticker, 'function');
  assert.equal(chart.data.datasets.length, 4);
  assert.deepEqual(order(), ['B', 'A']);
  assert.equal(label('B'), 'DL: 6.00');
  assert.equal(label('B', 'upload'), 'UL: 3.00');
  assert.equal(label('A'), 'DL: 4.00');
  assert.equal(label('A', 'upload'), 'UL: 2.00');
  assert.notEqual(series('A').borderColor, series('B').borderColor);
  assert.notEqual(series('A').borderColor, series('A', 'upload').borderColor);
  assert.notEqual(series('A', 'upload').borderColor, series('B', 'upload').borderColor);
  assert.deepEqual(Array.from(series('A', 'upload').borderDash), [5, 3]);
  assert.equal(series('A').fill, false);
  assert.equal(series('A').tension, 0);
  assert.equal(series('A').borderWidth, 1.5);
  assert.equal(series('A').pointBackgroundColor, series('A').borderColor);
  assert.deepEqual(series('B').peakPoint, { x: T0 - 500000, y: 6 });
  assert.deepEqual(series('B', 'upload').peakPoint, { x: T0 - 500000, y: 3 });
  assert.equal(series('B').pointRadius({ raw: series('B').peakPoint, dataset: series('B') }), 5);
  assert.equal(series('B').pointRadius({ raw: series('B').data[1], dataset: series('B') }), 0);
  console.log('PASS: moving chart starts with per-user colors, pinned peak markers and a legend ordered by peak speed');

  const before = state.updates.length;
  state.clock = T0 + 30000;
  state.ticker();
  assert.equal(chart.options.scales.x.max, T0 + 30000);
  assert.equal(chart.options.scales.x.min, T0 + 30000 - 600000);
  assert.equal(state.updates.length, before + 1);
  assert.equal(state.updates.at(-1), 'none');
  assert.deepEqual(order(), ['B', 'A']);
  state.clock = T0 + 120000;
  state.ticker();
  assert.equal(chart.options.scales.x.max, T0 + 120000);
  assert.deepEqual(order(), ['A', 'B']);
  assert.equal(label('A'), 'DL: 4.00');
  assert.equal(label('A', 'upload'), 'UL: 2.00');
  assert.equal(label('B'), 'DL: 3.00');
  assert.equal(label('B', 'upload'), 'UL: 1.50');
  assert.deepEqual(series('B').peakPoint, { x: T0 - 30000, y: 3 });
  state.hidden = true;
  const frozen = state.updates.length;
  state.clock += 1000;
  state.ticker();
  assert.equal(state.updates.length, frozen);
  state.hidden = false;
  console.log('PASS: the time window keeps moving right to left, expired peaks leave the legend and hidden tabs are not redrawn');

  state.live = { fresh: true, seconds: 5, sampled_at: T0 + 119000, users: [
    { user_key: 'A', status: 'active', download_rate: 8 * MB, upload_rate: 2 * MB },
    { user_key: 'B', status: 'offline', download_rate: 0, upload_rate: 0 },
    { user_key: 'C', status: 'active', download_rate: 5 * MB, upload_rate: 0 },
  ] };
  await state.refresh();
  await new Promise(setImmediate);
  assert.deepEqual(plain(series('A').data.slice(-3)), [[T0, 2], [T0 + 2000, null], [T0 + 119000, 8]]);
  assert.deepEqual(plain(series('A', 'upload').data.slice(-3)), [[T0, 1], [T0 + 2000, null], [T0 + 119000, 2]]);
  assert.equal(series('B').data.at(-1).x, T0 - 30000);
  assert.equal(chart.options.scales.y.max, Math.max(1, 8 * 1.1));
  const length = series('A').data.length;
  await state.refresh();
  await new Promise(setImmediate);
  assert.equal(series('A').data.length, length);
  assert.deepEqual(order(), ['A', 'B']);
  assert.equal(label('A'), 'DL: 8.00');
  state.history.sampledAt = T0 + 119000;
  state.history.users[0].points.push({ x: T0 + 2000, y: null }, { x: T0 + 119000, y: 8 });
  state.live = { ...state.live, sampled_at: T0 + 125000, users: [{ user_key: 'A', status: 'active', download_rate: MB, upload_rate: 0 }] };
  state.clock = T0 + 126000;
  await state.refresh();
  await new Promise(setImmediate);
  assert.deepEqual(plain(series('A').data.filter((point) => point.x >= T0 + 119000)), [[T0 + 119000, 8], [T0 + 121000, null], [T0 + 125000, 1]]);
  console.log('PASS: live samples extend the strip, insert gaps, ignore offline users and are replaced once history covers them');

  assert.ok(state.trafficRequests.some((request) => request.direction === 'download'));
  assert.ok(state.trafficRequests.some((request) => request.direction === 'upload'));
  state.clock = T0 + 130000;
  state.live = { fresh: true, seconds: 5, sampled_at: T0 + 129000, users: [{ user_key: 'A', status: 'active', download_rate: 9 * MB, upload_rate: 3 * MB }] };
  await state.refresh();
  assert.equal(series('A').data.at(-1).x, T0 + 129000);
  assert.equal(series('A').data.at(-1).y, 9);
  assert.equal(series('A', 'upload').data.at(-1).y, 3);

  const align = chart.options.scales.x.afterBuildTicks;
  for (const [minutes, step] of [[10, 120000], [30, 300000], [60, 600000], [90, 900000]]) {
    const scale = { min: T0 - minutes * 60000, max: T0 };
    align(scale);
    assert.ok(scale.ticks.length >= 4 && scale.ticks.length <= 7);
    assert.ok(scale.ticks.every((tick) => tick.value % step === 0 && tick.value >= scale.min && tick.value <= scale.max));
  }
  const untouched = { min: 5, max: 5 };
  align(untouched);
  assert.equal(untouched.ticks, undefined);

  const plugin = chart.config.plugins[0];
  const calls = [];
  const context = {
    save() { calls.push(['save']); },
    restore() { calls.push(['restore']); },
    measureText(text) { return { width: text.length * 6 }; },
    fillText(text, x, y) { calls.push(['fillText', text, x, y, this.fillStyle]); },
  };
  const frame = {
    ctx: context,
    chartArea: { left: 40, right: 400, top: 10, bottom: 200 },
    scales: { x: { getPixelForValue: (value) => 40 + (value - (T0 - 600000)) / 600000 * 360 }, y: { getPixelForValue: (value) => 200 - value * 20 } },
    data: { datasets: [
      { borderColor: '#f472b6', peakPoint: { x: T0 - 1000, y: 4 } },
      { borderColor: '#22d3ee', peakPoint: { x: T0 - 2000, y: 3.9 } },
      { borderColor: '#a3e635', peakPoint: null },
      { borderColor: '#fbbf24', peakPoint: { x: T0 - 300000, y: 1 } },
    ] },
  };
  plugin.afterDatasetsDraw(frame);
  const labels = calls.filter((call) => call[0] === 'fillText');
  assert.deepEqual(labels.map((call) => call[1]), ['4.00', '1.00']);
  assert.equal(labels[0][4], '#f472b6');
  assert.ok(labels[0][2] <= 400 - 14);
  assert.equal(calls[0][0], 'save');
  assert.equal(calls.at(-1)[0], 'restore');
  calls.length = 0;
  plugin.afterDatasetsDraw({ ...frame, data: { datasets: [{ peakPoint: null }] } });
  assert.equal(calls.length, 0);
  console.log('PASS: upload live points, clock-aligned ticks and non-overlapping peak labels');

  const calm = createMotionHarness({ reducedMotion: true });
  calm.state.clock = T0;
  calm.state.history = { sampledAt: T0, users: [{ user_key: 'A', user_name: 'Alice', peak_bytes_per_second: 2 * MB, points: [{ x: T0, y: 2 }] }] };
  await calm.start();
  assert.equal(calm.state.ticker, null);
  assert.deepEqual(calm.state.intervals, [1000]);
  assert.equal(calm.state.chart.options.scales.x.max, T0);
  assert.equal(calm.state.chart.data.datasets.length, 2);
  assert.equal(calm.legend.children.length, 1);
  console.log('PASS: reduced motion keeps the chart stepwise without the continuous timer');
}

async function main() {
  testUserCreationDialog();
  testHistoryResetDialog();
  await testHappChartMotion();
  const connections = element();
  const users = element();
  const topUsers = element();
  const liveRoot = element();
  const canvas = element();
  canvas.ariaLabel = 'Скорость Download и Upload пользователей HAPP, МБ в секунду';
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
  const directions = [];
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
  let historyAt = Date.parse('2026-10-08T00:01:13+00:00');
  let clock = historyAt;
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
    Date: class extends Date { static now() { return clock; } },
    URL,
    console,
    document,
    window: {
      Chart: FakeChart,
      location: { pathname: '/happ-server', href: 'http://localhost/happ-server', origin: 'http://localhost', reload() {} },
      addEventListener() {},
      setInterval(callback, delay) { if (delay === 1000) refresh = callback; return delay; },
      clearInterval() {},
      setTimeout() { return 1; },
      clearTimeout() {},
    },
    fetch: async (url, options) => {
      assert.ok(options.signal);
      if (url.startsWith('/happ-server/traffic?minutes=')) {
        const params = new URL(url, 'http://localhost').searchParams;
        const minutes = Number(params.get('minutes'));
        const direction = params.get('direction');
        ranges.push(minutes);
        directions.push(direction);
        const users = direction === 'upload' ? historyUsers.map((user) => ({ ...user, peak_bytes_per_second: user.peak_bytes_per_second === null ? null : user.peak_bytes_per_second / 2, points: user.points.map((point) => ({ ...point, y: point.y === null ? null : point.y / 2 })) })) : historyUsers;
        const result = { minutes, direction, since: historyAt - minutes * 60000, until: historyAt, sampled_at: historyAt, user_count: historyCount, users };
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
  assert.equal(chart.data.datasets.length, 4);
  assert.equal(chart.data.datasets[0].data[0].y, null);
  assert.equal(chart.data.datasets[1].data[0].y, null);
  assert.equal(legend.children[0].children[0].textContent, name);
  assert.equal(chart.options.animation, false);
  const colorFor = (key, direction) => chart.data.datasets.find((series) => series.userKey === key && series.direction === direction).borderColor;
  const colors = new Map(['personal-a', 'personal-b'].flatMap((key) => ['download', 'upload'].map((direction) => [`${key}:${direction}`, colorFor(key, direction)])));
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
  assert.equal(chart.data.datasets[2].data[0].y, .5);
  assert.equal(colorFor('personal-a', 'download'), colors.get('personal-a:download'));
  assert.equal(colorFor('personal-a', 'upload'), colors.get('personal-a:upload'));
  assert.equal(colorFor('personal-b', 'download'), colors.get('personal-b:download'));
  assert.equal(colorFor('personal-b', 'upload'), colors.get('personal-b:upload'));
  assert.equal(metrics.get('[data-happ-speed]').textContent, '1.50 МБ/с');
  assert.equal(legend.children[0].children[2].textContent, 'DL: 1.00');
  assert.equal(legend.children[0].children[4].textContent, 'UL: 0.50');
  assert.equal(legend.children[1].children[2].textContent, 'DL: 0.50');
  assert.equal(legend.children[1].children[4].textContent, 'UL: 0.25');
  assert.equal(chart.options.scales.y.max, 1.1);
  payload.sampled_at = '2026-10-08T00:00:03+00:00';
  payload.traffic_samples = [{ id: 'connection-a', user_key: 'personal-a', download_bytes: 0 }];
  await refresh();
  assert.equal(chart.data.datasets[0].data.at(-1).y, 0);
  assert.equal(chart.data.datasets[1].data.at(-1).y, 0);
  assert.equal(legend.children[0].children[2].textContent, 'DL: 1.00');
  assert.equal(legend.children[0].children[2].title, 'Пик Download за 30 минут: 1.00 МБ/с');
  assert.equal(chart.options.scales.y.max, 1.1);
  const length = chart.data.datasets[0].data.length;
  await refresh();
  assert.equal(chart.data.datasets[0].data.length, length);
  for (const minutes of [10, 30, 60, 90]) {
    range.value = String(minutes);
    await changeRange({ target: range });
    assert.equal(chart.options.scales.x.max - chart.options.scales.x.min, minutes * 60000);
    assert.equal(legend.children[0].children[2].textContent, 'DL: 1.00');
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
  assert.equal(colorFor('personal-a', 'download'), colors.get('personal-a:download'));
  assert.equal(colorFor('personal-b', 'upload'), colors.get('personal-b:upload'));
  assert.equal(new Set(chart.data.datasets.map((series) => series.borderColor)).size, 6);
  assert.ok(chart.data.datasets.every((series) => series.tension === 0 && series.fill === false));
  historyUsers = Array.from({ length: 12 }, (_, index) => ({ user_key: `user-${index}`, user_name: `User ${index}`, peak_bytes_per_second: 1048576, points: [{ x: historyAt, y: 1 }] }));
  historyCount = 12;
  await changeRange({ target: range });
  assert.equal(chart.data.datasets.length, 20);
  assert.equal(new Set(chart.data.datasets.map((series) => series.borderColor)).size, 20);
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
  assert.equal(legend.children[0].children[2].textContent, 'DL: 2.00');
  assert.equal(chart.options.scales.y.max, 2.2);
  const series = chart.data.datasets[0];
  const spikeAt = historyAt;
  historyAt += 6000;
  clock = historyAt;
  historyUsers[0].points.push({ x: historyAt, y: 0 });
  await refresh();
  await new Promise(setImmediate);
  assert.equal(chart.data.datasets[0], series);
  assert.equal(chart.options.animations, undefined);
  assert.equal(chart.options.scales.y.max, 2.2);
  assert.equal(chart.options.scales.x.max, historyAt);
  assert.equal(chart.options.scales.x.min, historyAt - 90 * 60000);
  assert.ok(series.data.some((point) => point.x === spikeAt && point.y === 2));
  historyAt += 6000;
  clock = historyAt;
  historyUsers[0].peak_bytes_per_second = 4194304;
  historyUsers[0].points.push({ x: historyAt, y: 4 });
  await refresh();
  await new Promise(setImmediate);
  assert.equal(chart.options.scales.y.max, 4.4);
  historyAt += 90 * 60000;
  clock = historyAt;
  historyUsers[0].peak_bytes_per_second = 0;
  historyUsers[0].points = [{ x: historyAt, y: 0 }];
  await refresh();
  await new Promise(setImmediate);
  assert.equal(chart.data.datasets[0], series);
  assert.equal(chart.options.scales.y.max, 4.4);
  await changeRange({ target: range });
  assert.equal(chart.options.scales.y.max, 1);
  console.log('PASS: persistent datasets, fixed vertical scale, larger peaks, horizontal time shift and explicit range reset');
  payload = { sampled_at: '2026-10-08T00:00:04+00:00', users: [{ user_key: 'personal-a', connections: 1 }], traffic_samples: [{ id: 'reconnected', user_key: 'personal-a', download_bytes: 90000000 }] };
  await refresh();
  assert.equal(metrics.get('[data-happ-speed]').textContent, '0.00 МБ/с');
  payload.sampled_at = '2026-10-08T00:00:05+00:00';
  payload.traffic_samples[0].download_bytes += 2097152;
  await refresh();
  assert.equal(metrics.get('[data-happ-speed]').textContent, '2.00 МБ/с');
  assert.ok([10, 30, 60, 90].every((minutes) => ranges.includes(minutes)));
  assert.ok(directions.includes('download') && directions.includes('upload'));
  console.log('PASS: HAPP live rates, persisted maxima, four ranges, request races, colors, limit and stale/empty states');
  payload.activity = { seconds: 5, fresh: true, sampled_at: historyAt, users: [
    { user_key: 'personal-a', user_name: name, status: 'active', connections: 3, ips: ['203.0.113.1', '2001:db8::1'], download_bytes: 1073741824, upload_bytes: 1048576, download_rate: 2097152, upload_rate: 1048576, download_peak: 4194304, upload_peak: 2097152 },
    { user_key: 'offline', user_name: 'Closed', status: 'offline', connections: 0, ips: [], download_bytes: 1073741824, upload_bytes: 1048576, download_rate: 0, upload_rate: 0, download_peak: null, upload_peak: null },
  ] };
  await refresh();
  assert.equal(activityRows.children.length, 2);
  const cells = activityRows.children[0].children;
  assert.equal(cells[0].children[0].textContent, name);
  assert.equal(cells[0].children[0].title, name);
  assert.equal(cells[0].children[2].textContent, 'Активен');
  assert.equal(cells[0].children[2].title, 'Активен');
  assert.equal(activityRows.children[1].children[0].children[2].textContent, 'Не подключён');
  const meter = cells[0].children[1];
  assert.equal(meter.role, 'meter');
  assert.equal(meter.ariaValueMax, '25');
  assert.equal(meter.ariaValueNow, '22');
  assert.equal(meter.children.length, 25);
  assert.equal(meter.children.filter((segment) => segment.className.includes(' lit')).length, 22);
  assert.equal(meter.children.filter((segment) => segment.className.includes(' edge')).length, 3);
  assert.equal(meter.children.filter((segment) => segment.className.includes('green')).length, 15);
  assert.equal(meter.children.filter((segment) => segment.className.includes('amber')).length, 5);
  assert.equal(meter.children.filter((segment) => segment.className.includes('red')).length, 5);
  assert.equal(activityRows.children[1].children[0].children[1].ariaValueNow, '0');
  assert.ok(activityRows.children[1].children[0].children[1].children.every((segment) => !segment.className.includes(' lit')));
  assert.equal(cells[1].textContent, '203.0.113.1, 2001:db8::1');
  assert.equal(cells[1].title, '203.0.113.1, 2001:db8::1');
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
  assert.equal(activityRows.children[0].children[0].children[1].ariaValueNow, '0');
  payload.activity.seconds = 60;
  payload.activity.users[0].status = 'connected';
  payload.activity.users[0].download_rate = 0;
  payload.activity.users[0].upload_rate = 0;
  await refresh();
  assert.equal(activityRows.children[0].children[0].children[2].textContent, 'Активен');
  assert.equal(activityRows.children[0].children[0].children[2].className, 'happ-activity-state active');
  assert.equal(activityRows.children[0].children[0].children[1].ariaValueNow, '0');
  let previousLevel = 0;
  payload.activity.users[0].status = 'active';
  for (const amount of [1, 1024, 65536, 1048576, 10 * 1048576, 100 * 1048576]) {
    payload.activity.users[0].download_rate = amount;
    await refresh();
    const current = activityRows.children[0].children[0].children[1];
    const level = Number(current.ariaValueNow);
    assert.ok(level >= previousLevel && level >= 1 && level <= 25);
    assert.equal(current.children.length, 25);
    assert.equal(current.children.filter((segment) => segment.className.includes(' lit')).length, level);
    previousLevel = level;
  }
  assert.equal(previousLevel, 25);
  payload.activity.fresh = false;
  await refresh();
  assert.equal(activityRows.children[0].children[0].children[1].ariaValueNow, '0');
  assert.equal(activityRows.children[0].children[0].children[2].textContent, 'Не подключён');
  assert.equal(activityRows.children[0].children[0].children[2].className, 'happ-activity-state offline');
  payload.activity.fresh = true;
  payload.activity.users[0].download_rate = 0;
  payload.activity.users[0].upload_rate = 1048576;
  await refresh();
  assert.ok(Number(activityRows.children[0].children[0].children[1].ariaValueNow) > 0);
  console.log('PASS: 25-segment activity meters, transparent idle/stale states, monotonic traffic levels, red zone and upload activity');
  assert.equal(metrics.get('[data-happ-peak-label]').textContent, 'за 60 сек, МБ/с');
  secondsControl.value = '61';
  await changeRange({ target: secondsControl });
  assert.equal(secondsControl.value, '60');
  assert.equal(chart.data.datasets.length, 2);
  assert.ok(canvas.ariaLabel.includes('Download и Upload'));
  httpStatus = 503;
  await refresh();
  assert.equal(metrics.get('[data-happ-current]').textContent, '— / —');
  assert.equal(activityRows.children[0].children[0].textContent, 'Ожидание свежих данных.');
  console.log('PASS: activity identity, IPs, lifetime totals, rates, peak window, dual-direction history, races and stale states');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});