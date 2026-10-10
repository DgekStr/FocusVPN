import html


def render_protocols_panel(settings, status, csrf, error='', host='', users=0):
    esc = html.escape
    trojan, hysteria2, trusttunnel = settings['trojan'], settings['hysteria2'], settings['trusttunnel']
    generated = not settings['cert_path']
    fingerprint = status.get('fingerprint') or ''
    certificate_note = (
        f'Самоподписанный EC P-256 · SHA-256 {esc(fingerprint[:16])}…' if generated and fingerprint
        else 'Сертификат будет создан при сохранении' if generated
        else f'Свой сертификат · SHA-256 {esc(fingerprint[:16])}…' if fingerprint else 'Свой сертификат недоступен'
    )
    service = str(status.get('service') or 'unknown')
    endpoint = (
        f'<span class="badge {"ok" if service == "active" else "bad"}">{esc(service)}</span>' if status.get('binary')
        else '<span class="badge bad">не установлен</span>'
    )
    banner = f'<div class="notice error" role="status">{esc(error)}</div>' if error else ''
    return f'''<section class="panel happ-protocols-panel"><h2>Протоколы HAPP: Trojan, Hysteria2 и TrustTunnel</h2>{banner}<form method="post" action="/settings/happ/protocols"><input type="hidden" name="csrf" value="{esc(csrf)}">
<p class="muted"><strong>Trojan</strong> маскируется под обычный HTTPS: TLS-соединение на отдельном порту с сертификатом сервера; ссылка добавляется в подписку HAPP. Совместим с Trojan и Trojan-Go клиентами в обычном TLS-режиме.</p>
<p class="muted"><strong>Hysteria2</strong> работает поверх QUIC (UDP) и сохраняет скорость на каналах с потерями пакетов; ссылка добавляется в подписку HAPP. Для самоподписанного сертификата ссылка содержит его SHA-256 (pinSHA256); если ваша версия HAPP не принимает такой сертификат, укажите сертификат доверенного центра ниже.</p>
<p class="muted"><strong>TrustTunnel</strong> — протокол AdGuard поверх HTTP/2 и HTTP/3 (QUIC): запросы идут отдельными потоками, поэтому одна потерянная посылка не задерживает остальные (head-of-line blocking). Ссылка tt:// открывается в приложении TrustTunnel; HAPP этот протокол не поддерживает (список поддерживаемых в HAPP: VLESS, VMess, Socks5, Trojan, Shadowsocks, Hysteria2), поэтому в подписку HAPP он не добавляется.</p>
<div class="form-grid"><div class="field"><label><input class="inline-checkbox" type="checkbox" name="trojan_enabled" value="on"{' checked' if trojan['enabled'] else ''}> Trojan (TCP)</label></div><div class="field"><label for="trojan_port">Порт Trojan</label><input id="trojan_port" name="trojan_port" type="number" min="1024" max="65535" value="{trojan['port']}" required></div>
<div class="field"><label><input class="inline-checkbox" type="checkbox" name="hysteria2_enabled" value="on"{' checked' if hysteria2['enabled'] else ''}> Hysteria2 (UDP)</label></div><div class="field"><label for="hysteria2_port">Порт Hysteria2</label><input id="hysteria2_port" name="hysteria2_port" type="number" min="1024" max="65535" value="{hysteria2['port']}" required></div>
<div class="field"><label><input class="inline-checkbox" type="checkbox" name="trusttunnel_enabled" value="on"{' checked' if trusttunnel['enabled'] else ''}> TrustTunnel (TCP + UDP)</label></div><div class="field"><label for="trusttunnel_port">Порт TrustTunnel</label><input id="trusttunnel_port" name="trusttunnel_port" type="number" min="1024" max="65535" value="{trusttunnel['port']}" required></div>
<div class="field"><label for="protocols_public_host">Публичный адрес в ссылках · пусто = адрес подписки</label><input id="protocols_public_host" name="public_host" value="{esc(settings['public_host'])}" placeholder="{esc(host)}" autocomplete="off"></div><div class="field"><label for="protocols_server_name">Имя TLS (SNI) · пусто = публичный адрес</label><input id="protocols_server_name" name="server_name" value="{esc(settings['server_name'])}" placeholder="{esc(settings['public_host'] or host)}" autocomplete="off"></div>
<div class="field full"><label for="protocols_cert_path">Сертификат (fullchain.pem) · пусто = самоподписанный</label><input id="protocols_cert_path" name="cert_path" value="{esc(settings['cert_path'])}" placeholder="/etc/sing-box-happ-server/tls/fullchain.pem" autocomplete="off"></div><div class="field full"><label for="protocols_key_path">Закрытый ключ (privkey.pem)</label><input id="protocols_key_path" name="key_path" value="{esc(settings['key_path'])}" placeholder="/etc/sing-box-happ-server/tls/privkey.pem" autocomplete="off"></div>
<div class="field full"><label><input class="inline-checkbox" type="checkbox" name="regenerate_cert" value="on"> Создать самоподписанный сертификат заново (ссылки Trojan, Hysteria2 и TrustTunnel нужно будет импортировать повторно)</label></div></div>
<p class="muted">TLS: {certificate_note}<br>TrustTunnel endpoint: {endpoint} · пользователей с доступом: {int(users)}</p>
<p class="muted">Откройте на роутере TCP {trojan['port']} (Trojan), UDP {hysteria2['port']} (Hysteria2) и TCP+UDP {trusttunnel['port']} (TrustTunnel) на этот сервер. Службы работают без привилегий и слушают порты от 1024: чтобы клиенты подключались к 443, перенаправьте внешний 443 на выбранный порт. Сертификат от доверенного центра сертификации укажите путями выше (файлы должны быть читаемы группой sing-box); после продления примените настройки повторно.</p>
<div class="actions"><button type="submit">Сохранить и применить</button></div><p class="muted">Применение перезапускает HAPP Server, активные сессии HAPP переподключатся. VIP-ссылка не меняется.</p></form></section>'''
