(() => {
  const views = new Set(['/', '/vless', '/outbounds', '/wireguard', '/happ-server', '/happ-history', '/settings', '/gateway-journal', '/about']);
  const basePath = document.body?.dataset?.vpnBase || '';
  const panelPath = (value) => basePath + value;
  const relativePath = (value) => basePath && value.startsWith(basePath + '/') ? value.slice(basePath.length) : value;
  const formSelector = (action) => `form[action="${panelPath(action)}"]`;
  let navigating = false;
  let wireGuardLiveTimer = null;
  let wireGuardLiveLoading = false;
  let happLiveTimer = null;
  let happLiveLoading = false;
  let happTraffic = null;
  let outboundCheckTimer = null;
  let outboundCheckLoading = false;
  let pendingGatewayForm = null;
  let happSetupDeferred = false;

  function animateFavicon() {
    const icons = [...document.querySelectorAll('link[rel~="icon"]')];
    if (!icons.length) return;
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    if (!context) return;
    canvas.width = 32;
    canvas.height = 32;
    const startedAt = Date.now();
    const draw = () => {
      const phase = ((Date.now() - startedAt) % 1500) / 1500;
      const radius = 5 + phase * 9;
      context.clearRect(0, 0, 32, 32);
      context.fillStyle = '#101820';
      context.beginPath();
      context.roundRect(0, 0, 32, 32, 8);
      context.fill();
      context.globalAlpha = 0.9 * (1 - phase);
      context.strokeStyle = '#34d399';
      context.lineWidth = 2;
      context.beginPath();
      context.arc(16, 16, radius, 0, Math.PI * 2);
      context.stroke();
      context.globalAlpha = 1;
      context.fillStyle = '#34d399';
      context.beginPath();
      context.arc(16, 16, 4.5 + Math.sin(phase * Math.PI * 2) * 0.5, 0, Math.PI * 2);
      context.fill();
      const frame = canvas.toDataURL('image/png');
      icons.forEach((icon) => {
        icon.type = 'image/png';
        icon.href = frame;
      });
    };
    draw();
    window.setInterval(draw, 120);
  }

  animateFavicon();

  function showHappSetup() {
    const dialog = document.querySelector('[data-happ-setup-dialog]');
    if (dialog && !dialog.open && !happSetupDeferred) dialog.showModal();
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-happ-setup-close]')) {
      happSetupDeferred = true;
      document.querySelector('[data-happ-setup-dialog]')?.close();
    }
  });

  function formatDateTime(value, missing = '—') {
    if (!value) return missing;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return missing;
    const pad = (part) => String(part).padStart(2, '0');
    return `${pad(date.getUTCDate())}.${pad(date.getUTCMonth() + 1)}.${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
  }

  function isViewLink(anchor) {
    if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) {
      return false;
    }
    const url = new URL(anchor.href, window.location.href);
    return url.origin === window.location.origin && views.has(relativePath(url.pathname));
  }

  function updateMenu(pathname) {
    pathname = relativePath(pathname);
    document.querySelectorAll('[data-panel-nav]').forEach((link) => {
      const active = link.dataset.panelNav === 'vless'
        ? pathname === '/' || pathname.startsWith('/vless')
        : pathname.startsWith(`/${link.dataset.panelNav}`);
      link.classList.toggle('active', active);
      link.setAttribute('aria-current', active ? 'page' : 'false');
    });
  }

  function renderWireGuardLive(payload) {
    const online = Number.isInteger(payload.online_count) ? payload.online_count : 0;
    const download = document.querySelector('[data-wg-download]');
    const upload = document.querySelector('[data-wg-upload]');
    const onlineCount = document.querySelector('[data-wg-online-count]');
    if (download && upload && typeof payload.download_mb === 'string' && typeof payload.upload_mb === 'string') {
      download.textContent = payload.download_mb;
      upload.textContent = payload.upload_mb;
    }
    if (onlineCount) {
      onlineCount.textContent = String(online);
    }
    const clientStates = new Map((Array.isArray(payload.clients) ? payload.clients : []).map((client) => [String(client.id), client]));
    document.querySelectorAll('[data-wg-client-id]').forEach((row) => {
      const client = clientStates.get(row.dataset.wgClientId);
      if (!client || !['online', 'stale', 'never'].includes(client.state)) return;
      const badge = row.querySelector('[data-wg-activity-badge]');
      const age = row.querySelector('[data-wg-activity-age]');
      if (badge) {
        badge.className = `badge ${client.state}`;
        badge.textContent = client.label || '';
      }
      if (age) age.textContent = client.age || '';
    });
    const dashboardOnline = document.querySelector('[data-wg-dashboard-online]');
    if (dashboardOnline) {
      dashboardOnline.className = `badge${online ? ' online' : ''}`;
      dashboardOnline.textContent = `${online} онлайн · ${dashboardOnline.dataset.wgEnabledCount || 0} включено`;
    }
    const onlineSummary = document.querySelector('[data-wg-online-summary]');
    if (onlineSummary) onlineSummary.textContent = `${online} онлайн`;
    const body = document.querySelector('[data-wg-connections]');
    if (!body) return;
    body.replaceChildren();
    const connections = Array.isArray(payload.connections) ? payload.connections : [];
    if (!connections.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.className = 'empty';
      cell.textContent = 'Нет активных подключений.';
      row.append(cell);
      body.append(row);
      return;
    }
    connections.forEach((connection) => {
      const row = document.createElement('tr');
      const name = happCell(connection.name);
      name.className = 'client-name';
      row.append(name, happCell(connection.ip), happCell(connection.wan_ip), happCell(connection.handshake), happCell(`${connection.download || '0 B'} / ${connection.upload || '0 B'}`), happCell(connection.keepalive));
      body.append(row);
    });
  }

  function syncWireGuardLive() {
    if (wireGuardLiveTimer) {
      window.clearInterval(wireGuardLiveTimer);
      wireGuardLiveTimer = null;
    }
    if (!document.querySelector('[data-wg-live]')) return;
    const refresh = async () => {
      if (wireGuardLiveLoading || document.hidden) return;
      wireGuardLiveLoading = true;
      try {
        const response = await fetch(panelPath('/wireguard/live'), { credentials: 'same-origin', cache: 'no-store' });
        if (response.status === 401) {
          window.location.reload();
          return;
        }
        if (!response.ok) return;
        renderWireGuardLive(await response.json());
      } catch (_) {
        // The next polling interval retries transient wg-easy failures.
      } finally {
        wireGuardLiveLoading = false;
      }
    };
    refresh();
    wireGuardLiveTimer = window.setInterval(refresh, 1000);
  }

  function happCell(value) {
    const cell = document.createElement('td');
    cell.textContent = value || '—';
    return cell;
  }

  function createHappTraffic() {
    const canvas = document.querySelector('[data-happ-traffic-chart]');
    if (!canvas || !window.Chart) return;
    const clock = (value) => new Date(value).toLocaleTimeString('ru-RU', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const now = Date.now();
    const chart = new window.Chart(canvas, {
      type: 'line', data: { datasets: [] },
      options: {
        responsive: true, maintainAspectRatio: false, parsing: false,
        animation: { duration: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 0 : 900, easing: 'linear' },
        animations: { y: { duration: 0 } },
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (items) => items.length ? clock(items[0].parsed.x) + ' UTC' : '', label: (item) => `${item.dataset.label}: ${item.parsed.y.toFixed(2)} МБ/с` } },
        },
        scales: {
          x: { type: 'linear', min: now - 600000, max: now, border: { display: false }, grid: { display: false }, ticks: { color: '#8ea0be', maxTicksLimit: 5, maxRotation: 0, font: { size: 10 }, callback: clock } },
          y: { beginAtZero: true, suggestedMax: 1, border: { display: false }, grid: { color: 'rgba(142,160,190,.12)' }, ticks: { color: '#8ea0be', maxTicksLimit: 5, font: { size: 10 }, callback: (value) => `${Number(value).toFixed(1)} МБ/с` } },
        },
      },
    });
    happTraffic = { chart, previous: new Map(), sampledAt: null, colors: new Map(), maximum: 0, minutes: 10, direction: 'download', seconds: 5, historyDue: 0, historyLoading: false, historyRequest: 0, historyController: null };
  }

  function happTrafficStatus(text, failed = false) {
    const status = document.querySelector('[data-happ-chart-status]');
    if (status) {
      status.textContent = text;
      status.className = failed ? 'badge bad' : 'badge online';
    }
  }

  function renderHappTraffic(payload) {
    if (!happTraffic) return;
    const timestamp = Date.parse(payload.sampled_at);
    if (!Number.isFinite(timestamp)) {
      happTrafficStatus('Нет свежих данных', true);
      return;
    }
    const state = happTraffic;
    if (state.sampledAt !== null && timestamp <= state.sampledAt) return;
    const elapsed = state.sampledAt === null ? 0 : (timestamp - state.sampledAt) / 1000;
    const measured = elapsed > 0 && elapsed <= 10;
    const users = new Map((Array.isArray(payload.users) ? payload.users : []).filter((user) => user.user_key && Number(user.connections) > 0).map((user) => [String(user.user_key), user]));
    const rates = new Map([...users.keys()].map((key) => [key, 0]));
    const samples = new Map();
    for (const item of Array.isArray(payload.traffic_samples) ? payload.traffic_samples : []) {
      const bytes = Number(item.download_bytes);
      if (!item.id || !item.user_key || !Number.isFinite(bytes) || bytes < 0) continue;
      const key = String(item.user_key);
      const previous = state.previous.get(String(item.id));
      samples.set(String(item.id), { key, bytes });
      if (measured && previous?.key === key && rates.has(key)) {
        rates.set(key, rates.get(key) + Math.max(0, bytes - previous.bytes) / elapsed / 1048576);
      }
    }
    state.previous = samples;
    state.sampledAt = timestamp;
    const total = document.querySelector('[data-happ-speed]');
    if (total) total.textContent = measured ? `${[...rates.values()].reduce((sum, rate) => sum + rate, 0).toFixed(2)} МБ/с` : '— МБ/с';
  }

  function renderHappTrafficHistory(payload, state) {
    const palette = ['#22d3ee', '#f472b6', '#5eead4', '#fbbf24', '#a78bfa', '#fb7185', '#38bdf8', '#a3e635', '#fb923c', '#c4b5fd'];
    const users = (Array.isArray(payload.users) ? payload.users : []).slice(0, 10);
    const reserved = new Set(users.map((user) => state.colors.get(String(user.user_key))).filter(Boolean));
    const used = new Set();
    const legend = document.querySelector('[data-happ-chart-legend]');
    legend?.replaceChildren();
    const previous = new Map(state.chart.data.datasets.map((dataset) => [dataset.userKey, dataset]));
    const datasets = [];
    let maximum = 0;
    for (const user of users) {
      const key = String(user.user_key);
      let color = state.colors.get(key);
      if (!color || used.has(color)) color = palette.find((value) => !reserved.has(value) && !used.has(value)) || palette.find((value) => !used.has(value));
      state.colors.set(key, color);
      used.add(color);
      const nameText = user.user_name || 'Не определён';
      const peak = user.peak_bytes_per_second === null ? null : Math.max(0, Number(user.peak_bytes_per_second) || 0) / 1048576;
      const points = Array.isArray(user.points) ? user.points : [];
      maximum = Math.max(maximum, peak || 0, ...points.map((point) => Number.isFinite(point.y) ? Math.max(0, point.y) : 0));
      const dataset = previous.get(key) || { userKey: key };
      Object.assign(dataset, { label: nameText, data: points, borderColor: color, backgroundColor: color + '0a', borderWidth: 2, pointRadius: 0, pointHitRadius: 8, tension: .3, cubicInterpolationMode: 'monotone', fill: true, spanGaps: false });
      datasets.push(dataset);
      if (legend) {
        const item = document.createElement('div');
        item.className = 'happ-chart-user';
        item.dataset.happUser = key;
        const swatch = document.createElement('span');
        swatch.className = 'happ-chart-swatch';
        swatch.style.backgroundColor = color;
        const name = document.createElement('strong');
        name.textContent = nameText;
        name.title = nameText;
        const amount = document.createElement('small');
        amount.textContent = peak === null ? 'Ожидание замера' : `Пик: ${peak.toFixed(2)} МБ/с`;
        amount.title = `Максимум за ${state.minutes} минут`;
        item.append(swatch, name, amount);
        legend.append(item);
      }
    }
    if (legend && !users.length) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = 'В выбранном отрезке ещё нет замеров скорости.';
      legend.append(empty);
    }
    const count = document.querySelector('[data-happ-chart-count]');
    const overflow = document.querySelector('[data-happ-chart-overflow]');
    if (count) count.textContent = `${users.length} / 10`;
    if (overflow) overflow.textContent = payload.user_count > 10 ? `Ещё ${payload.user_count - 10} пользователей в истории` : '';
    state.chart.data.datasets = datasets;
    state.chart.options.scales.x.min = payload.since;
    state.chart.options.scales.x.max = payload.until;
    state.maximum = Math.max(state.maximum, maximum);
    state.chart.options.scales.y.max = Math.max(1, state.maximum * 1.1);
    state.chart.update();
    const fresh = payload.sampled_at !== null && payload.until - payload.sampled_at <= 10000;
    happTrafficStatus(!users.length ? 'Нет замеров' : fresh ? 'Live' : 'Нет свежих данных', users.length > 0 && !fresh);
  }

  async function refreshHappTrafficHistory(force = false) {
    const state = happTraffic;
    if (!state || document.hidden || (!force && (state.historyLoading || Date.now() < state.historyDue))) return;
    if (force) state.historyController?.abort();
    const controller = new AbortController();
    state.historyController = controller;
    state.historyLoading = true;
    state.historyDue = Date.now() + 5000;
    const request = ++state.historyRequest;
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(panelPath(`/happ-server/traffic?minutes=${state.minutes}&direction=${state.direction}`), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
      if (response.status === 401) {
        window.location.reload();
        return;
      }
      if (!response.ok) throw new Error('History unavailable');
      const payload = await response.json();
      if (happTraffic === state && state.historyRequest === request && payload.minutes === state.minutes && (payload.direction || 'download') === state.direction) renderHappTrafficHistory(payload, state);
    } catch (_) {
      if (happTraffic === state && state.historyRequest === request) happTrafficStatus('История недоступна', true);
    } finally {
      window.clearTimeout(timeout);
      if (state.historyRequest === request) state.historyLoading = false;
    }
  }

  document.addEventListener('change', (event) => {
    const secondsControl = event.target.closest('[data-happ-peak-seconds]');
    if (secondsControl && happTraffic) {
      const seconds = Number(secondsControl.value);
      if (!Number.isInteger(seconds) || seconds < 1 || seconds > 60) {
        secondsControl.value = String(happTraffic.seconds);
        return;
      }
      happTraffic.seconds = seconds;
      renderHappActivity(null);
      return;
    }
    const directionControl = event.target.closest('[data-happ-chart-direction]');
    const control = event.target.closest('[data-happ-chart-range]');
    const minutes = control ? Number(control.value) : happTraffic?.minutes;
    if (!directionControl && !control) return;
    if (!happTraffic || ![10, 30, 60, 90].includes(minutes)) return;
    if (directionControl) {
      if (!['download', 'upload'].includes(directionControl.value)) return;
      happTraffic.direction = directionControl.value;
      const canvas = document.querySelector('[data-happ-traffic-chart]');
      if (canvas) canvas.ariaLabel = `Скорость ${happTraffic.direction === 'upload' ? 'отправки' : 'скачивания'} пользователей HAPP, МБ в секунду`;
    }
    happTraffic.minutes = minutes;
    happTraffic.maximum = 0;
    happTraffic.chart.data.datasets = [];
    happTraffic.chart.options.scales.y.max = 1;
    const until = Date.now();
    happTraffic.chart.options.scales.x.min = until - minutes * 60000;
    happTraffic.chart.options.scales.x.max = until;
    document.querySelector('[data-happ-chart-legend]')?.replaceChildren();
    const count = document.querySelector('[data-happ-chart-count]');
    const overflow = document.querySelector('[data-happ-chart-overflow]');
    if (count) count.textContent = '0 / 10';
    if (overflow) overflow.textContent = '';
    happTraffic.chart.update('none');
    happTrafficStatus('Загрузка истории');
    return refreshHappTrafficHistory(true);
  });

  function renderHappActivity(payload) {
    const body = document.querySelector('[data-happ-activity-users]');
    if (!body) return;
    const setText = (selector, text) => {
      const target = document.querySelector(selector);
      if (target) target.textContent = text;
    };
    const rate = (value) => value === null || value === undefined ? '—' : (Math.max(0, Number(value) || 0) / 1048576).toFixed(2);
    const bytes = (value) => {
      if (value === null || value === undefined) return '—';
      let amount = Math.max(0, Number(value) || 0);
      const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
      let unit = 0;
      while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++; }
      return `${amount.toFixed(unit ? 2 : 0)} ${units[unit]}`;
    };
    const users = Array.isArray(payload?.users) ? payload.users : [];
    const fresh = payload?.fresh === true && payload.seconds === happTraffic?.seconds;
    const connected = users.filter((user) => ['active', 'connected'].includes(user.status));
    const sum = (field) => users.reduce((total, user) => total + (Number(user[field]) || 0), 0);
    setText('[data-happ-activity-status]', fresh ? 'Обновлено ' + new Date(payload.sampled_at).toLocaleTimeString('ru-RU') : 'Нет свежих данных');
    setText('[data-happ-users-online]', fresh ? `${connected.length} / ${users.filter((user) => user.status === 'active').length}` : '— / —');
    setText('[data-happ-online]', fresh ? String(sum('connections')) : '—');
    const totalRate = (field) => connected.some((user) => user[field] === null || user[field] === undefined) ? null : sum(field);
    setText('[data-happ-current]', fresh ? `${rate(totalRate('download_rate'))} / ${rate(totalRate('upload_rate'))} МБ/с` : '— / —');
    if (payload) {
      setText('[data-happ-lifetime-download]', bytes(sum('download_bytes')));
      setText('[data-happ-lifetime-upload]', bytes(sum('upload_bytes')));
    }
    setText('[data-happ-peak-label]', `за ${happTraffic?.seconds || 5} сек, МБ/с`);
    body.replaceChildren();
    if (!users.length) {
      const row = document.createElement('tr');
      const cell = happCell(payload ? 'В журнале пока нет пользователей.' : 'Ожидание свежих данных.');
      cell.colSpan = 6;
      cell.className = 'empty';
      row.append(cell);
      body.append(row);
      return;
    }
    for (const user of users) {
      const row = document.createElement('tr');
      const identity = document.createElement('td');
      const name = document.createElement('strong');
      name.textContent = user.user_name || 'Не определён';
      name.title = name.textContent;
      const status = document.createElement('span');
      const state = fresh && ['active', 'connected'].includes(user.status) ? 'active' : 'offline';
      status.className = `happ-activity-state ${state}`;
      status.textContent = state === 'active' ? 'Активен' : 'Не подключён';
      status.title = status.textContent;
      const trafficRate = fresh && ['active', 'connected'].includes(state) ? [user.download_rate, user.upload_rate].reduce((total, value) => total + (Number.isFinite(value) ? Math.max(0, value) : 0), 0) : 0;
      const level = trafficRate > 0 ? Math.min(25, Math.ceil(25 * Math.log1p(trafficRate / 1024) / Math.log1p(10240))) : 0;
      const meter = document.createElement('span');
      meter.className = 'happ-activity-meter';
      meter.role = 'meter';
      meter.ariaLabel = `Сетевая активность ${name.textContent}`;
      meter.ariaValueMin = '0';
      meter.ariaValueMax = '25';
      meter.ariaValueNow = String(level);
      meter.ariaValueText = fresh ? `${rate(trafficRate)} МБ/с` : 'Нет свежих данных';
      meter.title = fresh ? `${rate(user.download_rate)} / ${rate(user.upload_rate)} МБ/с` : 'Нет свежих данных';
      for (let index = 0; index < 25; index++) {
        const segment = document.createElement('span');
        segment.className = `happ-activity-segment ${index < 15 ? 'green' : index < 20 ? 'amber' : 'red'}${index < level ? ' lit' : ''}${index < level && index >= level - 3 ? ' edge' : ''}`;
        segment.ariaHidden = 'true';
        segment.style.animationDelay = `${-index * 65}ms`;
        meter.append(segment);
      }
      identity.append(name, meter, status);
      const speed = happCell(fresh ? `${rate(user.download_rate)} / ${rate(user.upload_rate)}` : '— / —');
      const totals = happCell(`${bytes(user.download_bytes)} / ${bytes(user.upload_bytes)}`);
      totals.title = 'Накопленные замеры журнала; ручной сброс обнуляет итог.';
      if (user.unknown_traffic) {
        const missing = document.createElement('small');
        missing.textContent = `Без счётчиков: ${user.unknown_traffic}`;
        totals.append(missing);
      }
      const address = happCell(fresh ? (user.ips || []).join(', ') : '—');
      address.title = address.textContent;
      row.append(identity, address, happCell(fresh ? String(user.connections || 0) : '—'), totals, speed, happCell(fresh ? `${rate(user.download_peak)} / ${rate(user.upload_peak)}` : '— / —'));
      body.append(row);
    }
  }

  function renderHappLive(payload) {
    renderHappTraffic(payload);
    const online = Number.isInteger(payload.online_count) ? payload.online_count : 0;
    const count = document.querySelector('[data-happ-online-count]');
    const onlineMetric = document.querySelector('[data-happ-online]');
    const download = document.querySelector('[data-happ-download]');
    const upload = document.querySelector('[data-happ-upload]');
    if (count) count.textContent = `${online} онлайн`;
    if (onlineMetric) onlineMetric.textContent = String(online);
    if (download) download.textContent = payload.download || '0 B';
    if (upload) upload.textContent = payload.upload || '0 B';
    renderHappActivity(payload.activity);
    const topUsersBody = document.querySelector('[data-happ-top-users]');
    if (topUsersBody) {
      topUsersBody.replaceChildren();
      const topUsers = Array.isArray(payload.top_users) ? payload.top_users : [];
      if (!topUsers.length) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = 'Нет активных пользователей для рейтинга.';
        topUsersBody.append(empty);
      } else {
        const maxDownload = Math.max(1, ...topUsers.map((user) => Number(user.download_bytes) || 0));
        const maxUpload = Math.max(1, ...topUsers.map((user) => Number(user.upload_bytes) || 0));
        const appendMeasure = (container, label, value, bytes, maximum, direction) => {
          const measure = document.createElement('div');
          measure.className = 'happ-traffic-measure';
          const caption = document.createElement('span');
          caption.className = 'happ-traffic-label';
          caption.textContent = label;
          const track = document.createElement('span');
          track.className = 'happ-traffic-track';
          const fill = document.createElement('span');
          fill.className = `happ-traffic-fill ${direction}`;
          fill.style.width = `${Math.max(0, Math.min(100, (Number(bytes) || 0) / maximum * 100))}%`;
          track.append(fill);
          const amount = document.createElement('strong');
          amount.className = 'happ-traffic-value';
          amount.textContent = value || '0 B';
          measure.append(caption, track, amount);
          container.append(measure);
        };
        topUsers.forEach((user, index) => {
          const row = document.createElement('article');
          row.className = 'happ-traffic-rank';
          const identity = document.createElement('div');
          identity.className = 'happ-traffic-user';
          const place = document.createElement('span');
          place.className = 'happ-traffic-place';
          place.textContent = `0${index + 1}`;
          const name = document.createElement('strong');
          name.className = 'happ-traffic-user-name';
          name.textContent = user.user_name || 'Не определён';
          const connections = document.createElement('small');
          connections.textContent = `${user.connections || 0} соединений`;
          name.append(connections);
          identity.append(place, name);
          const measures = document.createElement('div');
          measures.className = 'happ-traffic-measures';
          appendMeasure(measures, 'Скачано', user.download, user.download_bytes, maxDownload, 'download');
          appendMeasure(measures, 'Отправлено', user.upload, user.upload_bytes, maxUpload, 'upload');
          row.append(identity, measures);
          topUsersBody.append(row);
        });
      }
    }
    if (payload.account_traffic) {
      document.querySelectorAll('[data-happ-account]').forEach((account) => {
        const totals = payload.account_traffic[account.dataset.happAccount] || {};
        const received = account.querySelector('[data-account-download]');
        const sent = account.querySelector('[data-account-upload]');
        if (received) received.textContent = totals.download || '0 B';
        if (sent) sent.textContent = totals.upload || '0 B';
      });
    }
    const usersBody = document.querySelector('[data-happ-user-traffic]');
    if (usersBody) {
      usersBody.replaceChildren();
      const users = Array.isArray(payload.users) ? payload.users : [];
      if (!users.length) {
        const row = document.createElement('tr');
        const cell = happCell('Нет активных подключений.');
        cell.colSpan = 4;
        cell.className = 'empty';
        row.append(cell);
        usersBody.append(row);
      }
      users.forEach((user) => {
        const row = document.createElement('tr');
        row.append(happCell(user.user_name || 'Не определён'), happCell(String(user.connections || 0)), happCell(user.download || '0 B'), happCell(user.upload || '0 B'));
        usersBody.append(row);
      });
    }
    const body = document.querySelector('[data-happ-connections]');
    if (!body) return;
    body.replaceChildren();
    const connections = Array.isArray(payload.connections) ? payload.connections : [];
    if (!connections.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 7;
      cell.className = 'empty';
      cell.textContent = 'Нет активных подключений.';
      row.append(cell);
      body.append(row);
      return;
    }
    connections.forEach((connection) => {
      const row = document.createElement('tr');
      const ip = happCell(connection.ip);
      ip.className = 'client-name';
      row.append(happCell(connection.user_name || 'Не определён'), ip, happCell(connection.duration), happCell(connection.download || '0 B'), happCell(connection.upload || '0 B'), happCell(connection.network), happCell(connection.destination));
      body.append(row);
    });
  }

  function syncHappLive() {
    if (happLiveTimer) {
      window.clearInterval(happLiveTimer);
      happLiveTimer = null;
    }
    if (happTraffic) {
      happTraffic.historyController?.abort();
      happTraffic.chart.destroy();
      happTraffic = null;
    }
    const root = document.querySelector('[data-happ-live]');
    if (!root) return;
    createHappTraffic();
    refreshHappTrafficHistory();
    if (!happTraffic && document.querySelector('[data-happ-traffic-chart]')) happTrafficStatus('График недоступен', true);
    const refresh = async () => {
      if (happLiveLoading || document.hidden || root.isConnected === false) return;
      happLiveLoading = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(panelPath(`/happ-server/live?seconds=${happTraffic?.seconds || 5}`), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (response.status === 401) {
          window.location.reload();
          return;
        }
        if (document.querySelector('[data-happ-live]') !== root) return;
        if (!response.ok) {
          renderHappActivity(null);
          happTrafficStatus('Нет свежих данных', true);
          return;
        }
        renderHappLive(await response.json());
      } catch (_) {
        if (document.querySelector('[data-happ-live]') === root) {
          renderHappActivity(null);
          happTrafficStatus('Нет свежих данных', true);
        }
      } finally {
        window.clearTimeout(timeout);
        happLiveLoading = false;
        if (document.querySelector('[data-happ-live]') === root) refreshHappTrafficHistory();
      }
    };
    refresh();
    happLiveTimer = window.setInterval(refresh, 1000);
  }

  function syncOutboundChecks() {
    if (outboundCheckTimer) {
      window.clearInterval(outboundCheckTimer);
      outboundCheckTimer = null;
    }
    if (!document.querySelector(`[data-outbound-checks], ${formSelector('/settings/vless-monitor')}`)) return;
    const refresh = async () => {
      if (outboundCheckLoading || document.hidden) return;
      outboundCheckLoading = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch(panelPath('/outbounds/checks'), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (response.status === 401) {
          window.location.reload();
          return;
        }
        if (!response.ok) throw new Error('Status refresh failed');
        const payload = await response.json();
        const monitorStatus = document.querySelector(formSelector('/settings/vless-monitor'))?.closest('section')?.querySelector('[data-monitor-status]');
        if (monitorStatus && payload.automation) {
          const automation = payload.automation;
          const text = `Последняя проверка: ${formatDateTime(automation.last_checked_at, 'ещё не выполнялась')}. Кандидат: ${automation.candidate || 'нет'} · ${automation.streak || 0}/3.${automation.running ? ' Цикл выполняется.' : ''}`;
          if (monitorStatus.textContent !== text) monitorStatus.textContent = text;
          const gatewayStatus = monitorStatus.parentElement?.querySelector('[data-gateway-status]');
          if (gatewayStatus && typeof automation.gateway_text === 'string' && gatewayStatus.textContent !== automation.gateway_text) gatewayStatus.textContent = automation.gateway_text;
        }
        const checks = new Map((Array.isArray(payload.checks) ? payload.checks : []).map((check) => [check.tag, check]));
        const summary = document.querySelector('[data-outbound-summary]');
        if (summary) {
          const tags = JSON.parse(summary.dataset.checkTags || '[]');
          const selected = tags.map((tag) => checks.get(tag)).filter(Boolean);
          const pending = selected.filter((check) => check.state === 'running' || check.state === 'queued');
          const finished = selected.filter((check) => check.state === 'success' || check.state === 'error');
          let text;
          let failed = false;
          if (pending.length) {
            text = `Проверено ${finished.length}/${selected.length}. Остальные проверки выполняются в фоне.`;
          } else if (selected.length === 1) {
            text = `${selected[0].tag}: ${selected[0].message}`;
            failed = selected[0].state !== 'success';
          } else {
            const failures = selected.filter((check) => check.state !== 'success');
            text = `Проверки завершены: ${finished.length}/${selected.length}.`;
            if (failures.length) text += ` Ошибка или нет результата: ${failures.map((check) => check.tag).join(', ')}.`;
            failed = failures.length > 0;
          }
          summary.textContent = (summary.dataset.checkPrefix || '') + text;
          summary.classList.toggle('success', pending.length === 0 && !failed);
          summary.classList.toggle('error', failed);
        }
        document.querySelectorAll('[data-outbound-check-tag]').forEach((cell) => {
          const check = checks.get(cell.dataset.outboundCheckTag);
          if (!check) return;
          cell.textContent = check.message || '';
          cell.dataset.checkState = check.state;
          const row = cell.closest('tr');
          row?.classList.toggle('outbound-failed', check.state === 'error');
          const latency = row?.querySelector('[data-outbound-latency]');
          const checkedAt = row?.querySelector('[data-outbound-checked-at]');
          if (latency) latency.textContent = typeof check.latency_ms === 'number' ? check.latency_ms.toFixed(2) : '—';
          if (checkedAt) checkedAt.textContent = formatDateTime(check.checked_at);
          const button = row?.querySelector(`${formSelector('/outbounds/check')} button`);
          if (button) button.disabled = check.state === 'running' || check.state === 'queued';
        });
      } catch (_) {
        const summary = document.querySelector('[data-outbound-summary]');
        if (summary) {
          summary.textContent = 'Не удалось обновить результаты проверки. Повторная попытка выполняется автоматически.';
          summary.classList.remove('success');
          summary.classList.add('error');
        }
      } finally {
        window.clearTimeout(timeout);
        outboundCheckLoading = false;
      }
    };
    refresh();
    outboundCheckTimer = window.setInterval(refresh, 1000);
  }

  function closeWireGuardQr() {
    const modal = document.querySelector('[data-wg-qr-modal]');
    const image = modal?.querySelector('[data-wg-qr-image]');
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    if (image) image.removeAttribute('src');
    document.body.style.removeProperty('overflow');
  }

  document.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-wg-qr-url]');
    const modal = document.querySelector('[data-wg-qr-modal]');
    const image = modal?.querySelector('[data-wg-qr-image]');
    if (opener && modal && image) {
      image.src = opener.dataset.wgQrUrl;
      image.alt = `QR-конфигурация WireGuard: ${opener.dataset.wgQrLabel || ''}`;
      const title = modal.querySelector('[data-wg-qr-title]');
      const caption = modal.querySelector('[data-wg-qr-caption]');
      if (title) title.textContent = opener.dataset.wgQrLabel || 'QR WireGuard';
      if (caption) caption.textContent = 'Отсканируйте код в приложении WireGuard.';
      modal.hidden = false;
      modal.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      modal.querySelector('[data-wg-qr-close]')?.focus();
      return;
    }
    if (event.target.closest('[data-wg-qr-close]') || event.target === modal) closeWireGuardQr();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeWireGuardQr();
  });

  document.addEventListener('click', (event) => {
    const dialog = document.querySelector('[data-outbound-edit-dialog]');
    if (!dialog) return;
    const opener = event.target.closest('[data-outbound-edit]');
    if (opener) {
      const profile = JSON.parse(opener.dataset.outboundEdit);
      dialog.querySelector('[data-outbound-edit-tag]').value = profile.tag;
      const editor = dialog.querySelector('[data-outbound-edit-json]');
      editor.value = JSON.stringify(profile, null, 2);
      editor.setCustomValidity('');
      const submit = dialog.querySelector('button[type="submit"]');
      submit.disabled = false;
      submit.textContent = 'Сохранить';
      dialog.showModal();
      editor.focus();
    } else if (event.target.closest('[data-outbound-edit-cancel]')) {
      dialog.close();
    }
  });

  document.addEventListener('input', (event) => {
    if (event.target.matches('[data-outbound-edit-json]')) event.target.setCustomValidity('');
  });

  document.addEventListener('submit', (event) => {
    if (event.target.matches('[data-outbound-edit-form]')) {
      const editor = event.target.querySelector('[data-outbound-edit-json]');
      try {
        const profile = JSON.parse(editor.value);
        if (!profile || Array.isArray(profile) || typeof profile !== 'object') throw new Error();
      } catch (_) {
        event.preventDefault();
        editor.setCustomValidity('Введите корректный JSON одного сервера.');
        editor.reportValidity();
        return;
      }
    }
    if (event.target.matches(formSelector('/outbounds/import'))) {
      const button = event.target.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
        button.textContent = event.target.matches('[data-outbound-edit-form]') ? 'Сохраняется…' : 'Импортируется…';
      }
      return;
    }
    const deleteForm = event.target.closest('form[data-outbound-delete]');
    const deleteDialog = document.querySelector('[data-outbound-delete-dialog]');
    if (deleteForm && deleteForm.dataset.confirmed !== 'true' && deleteDialog) {
      event.preventDefault();
      pendingGatewayForm = deleteForm;
      const name = deleteForm.querySelector('input[name="tag"]')?.value || '';
      const message = deleteDialog.querySelector('[data-outbound-delete-message]');
      if (message) message.textContent = `Профиль ${name} будет удалён из конфигурации и selector.`;
      deleteDialog.showModal();
      deleteDialog.querySelector('[data-outbound-delete-confirm]')?.focus();
      return;
    }
    const form = event.target.closest('form[data-gateway-mode]');
    const dialog = document.querySelector('[data-gateway-dialog]');
    if (!form || form.dataset.confirmed === 'true' || !dialog) return;
    event.preventDefault();
    pendingGatewayForm = form;
    const targetMode = form.dataset.gatewayMode;
    const title = dialog.querySelector('[data-gateway-dialog-title]');
    const message = dialog.querySelector('[data-gateway-dialog-message]');
    const confirmations = {
      vless: ['Переключить на VLESS?', 'Шлюз по умолчанию или внешний WireGuard остановится. VPN-клиенты вернутся на VLESS; действующие соединения переподключатся.'],
      default: ['Переключить на шлюз по умолчанию?', 'VLESS/TPROXY и внешний WireGuard остановятся. Внешний трафик VPN-клиентов пойдёт через основной шлюз сервера; локальные сети и индивидуальные LAN-запреты сохраняются.']
    };
    const [titleText, messageText] = confirmations[targetMode] || confirmations.vless;
    if (title) title.textContent = titleText;
    if (message) message.textContent = messageText;
    dialog.showModal();
    dialog.querySelector('[data-gateway-dialog-confirm]')?.focus();
  });

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-outbound-delete-confirm]') && pendingGatewayForm) {
      const form = pendingGatewayForm;
      pendingGatewayForm = null;
      document.querySelector('[data-outbound-delete-dialog]')?.close();
      form.dataset.confirmed = 'true';
      form.requestSubmit();
      return;
    }
    if (!event.target.closest('[data-gateway-dialog-confirm]') || !pendingGatewayForm) return;
    const form = pendingGatewayForm;
    pendingGatewayForm = null;
    document.querySelector('[data-gateway-dialog]')?.close();
    form.dataset.confirmed = 'true';
    form.requestSubmit();
  });

  async function navigate(url, pushState) {
    if (navigating) return;
    const target = new URL(url, window.location.href);
    if (target.origin !== window.location.origin || !views.has(relativePath(target.pathname))) return;
    const current = new URL(window.location.href);
    if (target.pathname === current.pathname && target.search === current.search && target.hash === current.hash) {
      updateMenu(target.pathname);
      return;
    }
    navigating = true;
    try {
      const response = await fetch(target.href, {
        credentials: 'same-origin',
        headers: { 'X-Panel-Navigation': 'fragment' },
      });
      if (response.status === 401) {
        window.location.reload();
        return;
      }
      if (!response.ok) throw new Error(`Navigation failed: ${response.status}`);
      const markup = await response.text();
      const nextDocument = new DOMParser().parseFromString(markup, 'text/html');
      const nextMain = nextDocument.querySelector('main');
      const currentMain = document.querySelector('main');
      if (!nextMain || !currentMain) {
        window.location.href = target.href;
        return;
      }
      currentMain.replaceWith(nextMain);
      showHappSetup();
      document.title = nextDocument.title;
      updateMenu(target.pathname);
      syncWireGuardLive();
      syncHappLive();
      syncOutboundChecks();
      if (pushState) window.history.pushState({}, '', target.href);
      window.scrollTo({ top: 0, behavior: 'instant' });
    } catch (error) {
      console.error(error);
      window.location.href = target.href;
    } finally {
      navigating = false;
    }
  }

  document.addEventListener('click', (event) => {
    const anchor = event.target.closest('a');
    if (!isViewLink(anchor)) return;
    event.preventDefault();
    navigate(anchor.href, true);
  });

  window.addEventListener('popstate', () => navigate(window.location.href, false));
  updateMenu(window.location.pathname);
  syncWireGuardLive();
  syncHappLive();
  syncOutboundChecks();
  showHappSetup();
})();
