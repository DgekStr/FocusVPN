(() => {
  const views = new Set(['/', '/vless', '/outbounds', '/wireguard', '/happ-server', '/happ-history', '/settings', '/gateway-journal']);
  const basePath = document.body?.dataset?.vpnBase || '';
  const panelPath = (value) => basePath + value;
  const relativePath = (value) => basePath && value.startsWith(basePath + '/') ? value.slice(basePath.length) : value;
  const formSelector = (action) => `form[action="${panelPath(action)}"]`;
  let navigating = false;
  let wireGuardLiveTimer = null;
  let wireGuardLiveLoading = false;
  let happLiveTimer = null;
  let happLiveLoading = false;
  let outboundCheckTimer = null;
  let outboundCheckLoading = false;
  let pendingGatewayForm = null;

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

  function renderHappLive(payload) {
    const online = Number.isInteger(payload.online_count) ? payload.online_count : 0;
    const count = document.querySelector('[data-happ-online-count]');
    const onlineMetric = document.querySelector('[data-happ-online]');
    const download = document.querySelector('[data-happ-download]');
    const upload = document.querySelector('[data-happ-upload]');
    if (count) count.textContent = `${online} онлайн`;
    if (onlineMetric) onlineMetric.textContent = String(online);
    if (download) download.textContent = payload.download || '0 B';
    if (upload) upload.textContent = payload.upload || '0 B';
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
    if (!document.querySelector('[data-happ-live]')) return;
    const refresh = async () => {
      if (happLiveLoading || document.hidden) return;
      happLiveLoading = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(panelPath('/happ-server/live'), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (response.status === 401) {
          window.location.reload();
          return;
        }
        if (!response.ok) return;
        renderHappLive(await response.json());
      } catch (_) {
        // The next polling interval retries temporary sing-box API failures.
      } finally {
        window.clearTimeout(timeout);
        happLiveLoading = false;
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
        const monitorStatus = document.querySelector(formSelector('/settings/vless-monitor'))?.closest('section')?.querySelector('p.muted');
        if (monitorStatus && payload.automation) {
          const automation = payload.automation;
          const text = `Последняя проверка: ${formatDateTime(automation.last_checked_at, 'ещё не выполнялась')}. Кандидат: ${automation.candidate || 'нет'} · ${automation.streak || 0}/3.${automation.running ? ' Цикл выполняется.' : ''}`;
          if (monitorStatus.textContent !== text) monitorStatus.textContent = text;
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

  document.addEventListener('submit', (event) => {
    if (event.target.matches(formSelector('/outbounds/import'))) {
      const button = event.target.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
        button.textContent = 'Импортируется…';
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
      wireguard: ['Переключить на внешний WireGuard?', 'VLESS/TPROXY остановится. Весь внешний трафик VPN-клиентов пойдёт через внешний WG-туннель; локальные сети останутся напрямую.'],
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
})();
