(() => {
  document.addEventListener('click', (event) => {
    const dialog = document.querySelector('[data-happ-user-create-dialog]');
    if (!dialog) return;
    if (event.target.closest('[data-happ-user-create-open]')) {
      event.preventDefault();
      dialog.querySelector('form')?.reset();
      dialog.showModal();
      dialog.querySelector('[name="name"]')?.focus();
    } else if (event.target.closest('[data-happ-user-create-cancel]')) {
      event.preventDefault();
      dialog.close();
    } else if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
        dialog.close();
      }
    }
  });

  function copyLink(link, button, successText = 'Скопировано') {
    const originalText = button.textContent;
    const done = () => {
      button.textContent = successText;
      window.setTimeout(() => { button.textContent = originalText; }, 1800);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(link).then(done).catch(() => fallbackCopy(link, done));
      return;
    }
    fallbackCopy(link, done);
  }

  function fallbackCopy(link, done) {
    const area = document.createElement('textarea');
    area.value = link;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
    done();
  }

  function openApp(link, button) {
    let leftPage = false;
    const onLeave = () => {
      leftPage = true;
      document.removeEventListener('visibilitychange', onLeave);
      window.removeEventListener('blur', onLeave);
    };
    document.addEventListener('visibilitychange', onLeave);
    window.addEventListener('blur', onLeave);
    window.location.assign(link);
    window.setTimeout(() => {
      document.removeEventListener('visibilitychange', onLeave);
      window.removeEventListener('blur', onLeave);
      if (!leftPage) copyLink(link, button, 'Ссылка скопирована');
    }, 900);
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-happ-action]');
    if (!button) return;
    const link = button.dataset.happLink;
    if (!link) return;
    if (button.dataset.happAction === 'open') {
      event.preventDefault();
      openApp(link, button);
    }
    if (button.dataset.happAction === 'copy') copyLink(link, button);
  });

  function closeQr() {
    const modal = document.querySelector('[data-qr-modal]');
    const image = modal?.querySelector('[data-qr-image]');
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    if (image) image.removeAttribute('src');
    document.body.style.removeProperty('overflow');
  }

  document.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-qr-open]');
    const modal = document.querySelector('[data-qr-modal]');
    const image = modal?.querySelector('[data-qr-image]');
    const caption = modal?.querySelector('[data-qr-caption]');
    if (opener && modal && image) {
      event.preventDefault();
      const label = opener.dataset.qrLabel || 'VIP-ссылка';
      const qrUrl = new URL(opener.dataset.qrUrl || `${document.body?.dataset?.vpnBase || ''}/happ-qr`, window.location.href);
      qrUrl.searchParams.set('v', '4');
      image.src = qrUrl.href;
      image.alt = `QR подписки HAPP: ${label}`;
      if (caption) caption.textContent = `Мобильная подписка HAPP: ${label}`;
      modal.hidden = false;
      modal.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      modal.querySelector('[data-qr-close]')?.focus();
      return;
    }
    if (event.target.closest('[data-qr-close]') || event.target === modal) closeQr();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      const dialog = document.querySelector('[data-happ-user-create-dialog]');
      if (dialog?.open) dialog.close();
      closeQr();
    }
  });

  document.addEventListener('submit', (event) => {
    const trafficForm = event.target.closest('form[data-happ-traffic-reset]');
    if (trafficForm && trafficForm.dataset.confirmed !== 'true') {
      event.preventDefault();
      const dialog = document.querySelector('[data-happ-traffic-reset-dialog]');
      if (!dialog) return;
      const name = trafficForm.closest('tr')?.querySelector('td strong')?.textContent || 'пользователя';
      const message = dialog.querySelector('[data-happ-traffic-reset-message]');
      if (message) message.textContent = `Накопительные счётчики пользователя ${name} будут сброшены. Остальные профили не изменятся.`;
      dialog.showModal();
      const confirm = dialog.querySelector('[data-happ-traffic-reset-confirm]');
      confirm.onclick = () => {
        dialog.close();
        trafficForm.dataset.confirmed = 'true';
        trafficForm.requestSubmit();
      };
      return;
    }
    const basePath = document.body?.dataset?.vpnBase || '';
    const form = event.target.closest(`form[action="${basePath}/happ-users/action"]`);
    if (!form || event.submitter?.value !== 'delete' || form.dataset.confirmed === 'true') return;
    event.preventDefault();
    const dialog = document.querySelector('[data-happ-user-delete-dialog]');
    if (!dialog) return;
    dialog.showModal();
    const confirm = dialog.querySelector('[data-happ-user-delete-confirm]');
    confirm.onclick = () => {
      dialog.close();
      form.dataset.confirmed = 'true';
      form.requestSubmit(event.submitter);
    };
  });
})();
