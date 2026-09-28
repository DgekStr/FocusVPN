(() => {
  const views = new Set(['/', '/wireguard', '/happ-routing', '/happ-server']);
  let navigating = false;
  const routeStyleCache = new Map();

  function isViewLink(anchor) {
    if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) {
      return false;
    }
    const url = new URL(anchor.href, window.location.href);
    return url.origin === window.location.origin && views.has(url.pathname);
  }

  function setRouteStyles(documentFragment, path) {
    const existing = document.getElementById('panel-route-style');
    if (path === '/') {
      existing?.remove();
      return;
    }
    let styles = routeStyleCache.get(path);
    if (styles === undefined) {
      styles = Array.from(documentFragment.head.querySelectorAll('style'))
        .map((style) => style.textContent || '')
        .join('\n');
      routeStyleCache.set(path, styles);
    }
    if (!styles) {
      existing?.remove();
      return;
    }
    if (existing?.dataset.routePath === path) return;
    const routeStyle = existing || document.createElement('style');
    routeStyle.id = 'panel-route-style';
    routeStyle.dataset.routePath = path;
    routeStyle.textContent = styles;
    if (!existing) {
      document.head.appendChild(routeStyle);
    }
  }

  function updateMenu(pathname) {
    document.querySelectorAll('[data-panel-nav]').forEach((link) => {
      const active = link.dataset.panelNav === 'vless' ? pathname === '/' : pathname.startsWith(`/${link.dataset.panelNav}`);
      link.classList.toggle('active', active);
      link.setAttribute('aria-current', active ? 'page' : 'false');
    });
  }

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
      setRouteStyles(nextDocument, target.pathname);
      currentMain.replaceWith(nextMain);
      document.title = nextDocument.title;
      updateMenu(target.pathname);
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
})();
