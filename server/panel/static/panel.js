(() => {
  const views = new Set(['/', '/vless', '/wireguard', '/happ-server', '/settings']);
  let navigating = false;
  let wireGuardLiveTimer = null;
  let wireGuardLiveLoading = false;
  let happLiveTimer = null;
  let happLiveLoading = false;
  let pendingGatewayForm = null;

  function isViewLink(anchor) {
    if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) {
      return false;
    }
    const url = new URL(anchor.href, window.location.href);
    return url.origin === window.location.origin && views.has(url.pathname);
  }

  function updateMenu(pathname) {
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
        const response = await fetch('/wireguard/live', { credentials: 'same-origin', cache: 'no-store' });
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
    const body = document.querySelector('[data-happ-connections]');
    if (!body) return;
    body.replaceChildren();
    const connections = Array.isArray(payload.connections) ? payload.connections : [];
    if (!connections.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 5;
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
      row.append(ip, happCell(connection.duration), happCell(`${connection.download || '0 B'} / ${connection.upload || '0 B'}`), happCell(connection.network), happCell(connection.destination));
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
      try {
        const response = await fetch('/happ-server/live', { credentials: 'same-origin', cache: 'no-store' });
        if (response.status === 401) {
          window.location.reload();
          return;
        }
        if (!response.ok) return;
        renderHappLive(await response.json());
      } catch (_) {
        // The next polling interval retries temporary sing-box API failures.
      } finally {
        happLiveLoading = false;
      }
    };
    refresh();
    happLiveTimer = window.setInterval(refresh, 1000);
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
    const form = event.target.closest('form[data-gateway-mode]');
    const dialog = document.querySelector('[data-gateway-dialog]');
    if (!form || form.dataset.confirmed === 'true' || !dialog) return;
    event.preventDefault();
    pendingGatewayForm = form;
    const useWireGuard = form.dataset.gatewayMode === 'wireguard';
    const title = dialog.querySelector('[data-gateway-dialog-title]');
    const message = dialog.querySelector('[data-gateway-dialog-message]');
    if (title) title.textContent = useWireGuard ? 'Переключить на внешний WireGuard?' : 'Вернуться на VLESS?';
    if (message) message.textContent = useWireGuard
      ? 'VLESS остановится, действующие соединения переподключатся. Весь внешний трафик VPN-клиентов пойдёт через внешний сервер; локальная сеть останется доступна напрямую.'
      : 'Внешний WireGuard остановится, а VPN-клиенты вернутся на VLESS. Действующие соединения переподключатся.';
    dialog.showModal();
    dialog.querySelector('[data-gateway-dialog-confirm]')?.focus();
  });

  document.addEventListener('click', (event) => {
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
    if (target.origin !== window.location.origin || !views.has(target.pathname)) return;
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
})();
