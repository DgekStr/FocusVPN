# Operations

## Production paths

```text
/opt/sing-box-admin/
/etc/sing-box/config.json
/etc/sing-box-happ-server/config.json
/etc/sing-box-admin/
/etc/focusvpn/gateway-mode.json
/usr/local/libexec/
/etc/systemd/system/
```

В репозитории production values заменены placeholders. Не копируйте `.example` поверх runtime без ручного заполнения секретов.

## Service status

```bash
systemctl is-active sing-box sing-box-gateway sing-box-admin sing-box-happ-server
systemctl is-active wg-easy-private-ui
systemctl is-enabled sing-box sing-box-admin sing-box-ru-zone-update.timer
ss -ltnp | grep -E '9443|9445|12345'
```

## Gateway Modes

Version 2.1.9 removes the external WireGuard client. Settings provides only VLESS and the server's ordinary default gateway. WireGuard server management, client accounts, QR/config export and per-client LAN restrictions remain available.

Before updating a legacy installation, privately back up and disable its external client unit, retire only its owned routing/NAT artifacts, and remove the working/input/status files. Preserve the managed WG server, HAPP credentials and subscription registry. Do not remove the system's wg-quick template or change the main default route. Keep private backups outside the repository.

## VPN Host Metrics

Settings shows the VPN host LAN address and OS, uptime, root filesystem usage (total/used GiB, shown as ГБ), rolling 24-hour CPU/LAN peaks, and OS RX/TX byte counters since boot. In an LXC container the panel unit must keep `BindReadOnlyPaths=/proc/uptime` (alongside `ProtectKernelTunables=true`), otherwise the panel namespace reads the physical host's uptime; compare `cat /proc/uptime` with `nsenter -t <panel-pid> -m cat /proc/uptime`. The five overview cards share one row on wide screens. The panel samples `/proc/stat`, `/proc/net/dev`, and `/proc/uptime` every five seconds; physical interfaces are preferred, with virtual interfaces excluded. Samples are stored in `/mnt/stat/server-metrics.sqlite3` (directory mode `0700`, database mode `0600`) and pruned after 24 hours. CPU/LAN peak history begins when this collector is installed; RX/TX totals reset when the operating system reboots. These are host metrics, not billing counters.

## Validation

```bash
sing-box check -C /etc/sing-box
sing-box check -C /etc/sing-box-happ-server
nft --check -f /etc/sing-box/tproxy.nft
systemd-analyze verify /etc/systemd/system/sing-box-admin.service
```

## Panel deployment

For a clean Debian 12+/Ubuntu 22.04+ host, the standalone executable bootstrap entry point is:

```bash
curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/v2.1.7/scripts/bootstrap.sh | sudo bash -s -- v2.1.7
```

До любых изменений `bootstrap.sh` показывает пакеты и службы и требует явного подтверждения. Недостающие пакеты устанавливаются только после согласия; далее bootstrap клонирует выбранный ref и запускает installer. Пароль панели FocusVPN подтверждается через TTY и хранится как scrypt hash с правами `0600`. Installer проверяет sing-box по SHA-256 точного release asset, настраивает Nginx HTTPS `:7445`, запускает панель и wg-easy. Служебный администратор wg-easy создаётся автоматически, без ввода пароля; API проверяется установщиком, прямой доступ к `51821` извне запрещён. При старой WireGuard-установке показывается отдельное предупреждение: согласие сохраняет конфиги в приватную резервную копию, отказ отменяет установку без очистки. Sing-box/HAPP запускаются только с `--enable` после настройки реальных конфигураций.

For a clean-clone smoke without changing a server, clone the same public ref into a temporary directory, check that `VERSION`, `scripts/bootstrap.sh`, `scripts/install.sh`, panel/helper files and packaged systemd units exist, run the Python/Node/publication suites from that checkout, and invoke `scripts/install.sh --help`. A full OS/service startup test requires a disposable Debian/Ubuntu VM; unit tests on Windows do not emulate apt/systemd or start VPN networking.

For the published v2.1.7 Git ref on Windows/PowerShell, run the smoke from a fresh clone:

```powershell
$clone = Join-Path $env:TEMP ('FocusVPN-v2.1.7-' + [guid]::NewGuid().ToString('N'))
git clone --depth 1 --branch v2.1.7 https://github.com/DgekStr/FocusVPN.git $clone
Push-Location $clone
try {
	.\scripts\validate.ps1
	py -3 -m unittest discover -s scripts -p 'test_*.py' -q
	node scripts\test_panel_checks.js
	node scripts\test_happ_stats_ui.js
	& 'C:\Program Files\Git\bin\bash.exe' scripts/bootstrap.sh --help
	& 'C:\Program Files\Git\bin\bash.exe' scripts/install.sh --help
} finally {
	Pop-Location
}
```

This validates the published Git ref and installer entry points without running privileged installation. A full apt/systemd first boot must still be checked in a disposable Debian/Ubuntu VM.

For a clean Debian 12+/Ubuntu 22.04+ host, the standalone entry point is:

```bash
curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/v2.1.7/scripts/bootstrap.sh | sudo bash -s -- v2.1.7
```

Начиная с v2.1.5, первоначальная настройка wg-easy выполняется автоматически через официальный `INIT_*` механизм. Служебный администратор `focusvpn-service` получает уникальный пароль, сохранённый в `/etc/sing-box-admin/wg-easy-api.json` с правами `0600`. Installer проверяет `/api/client`, пересоздаёт контейнер без INIT-переменных и снова проверяет API. Пароль не печатается и не включается в Git; уже настроенная база не сбрасывается, и несовпадение существующих credentials останавливает установку. Вход в панель FocusVPN остаётся отдельным и интерактивным. Для удалённых WireGuard клиентов проверьте публичный endpoint и проброс UDP `51820`.

To verify the clone/deploy path without touching the production host, run `git clone --branch <ref> --depth 1 https://github.com/DgekStr/FocusVPN.git <temporary-dir>`, then execute `scripts/validate.ps1` and the documented Python/Node suites from that checkout. The bootstrap uses only the cloned repository; production runtime credentials are not sourced from Git.

The default outbound and server profiles are shared between the gateway and HAPP configuration. Applying a server or route change validates and backs up both files, then restarts both services in VLESS mode. In WireGuard mode HAPP retains its marked direct outbound; the chosen provider route is stored for the return to VLESS. Regression check: `py -3 scripts/test_route_sync.py`.

Server JSON import queues `focusvpn-outbound-test@<tag>.service` checks after saving validated profiles and returns without waiting for network tests. An isolated sing-box process with a loopback SOCKS listener checks HTTPS egress without switching the production route or holding the configuration lock. The manager polls the authenticated `/outbounds/checks` endpoint and displays queued, running, success or error status per profile. Only a sanitized result is stored under `/etc/sing-box-admin/outbound-checks/`. The manager provides a repeat-check button; TLS verification stays enabled unless explicitly configured otherwise in the imported profile.

1. Copy `server/panel/*.py` to `/opt/sing-box-admin/`.
2. Copy `server/panel/static/` to `/opt/sing-box-admin/static/`.
3. Keep root-only files `auth.json`, `wg-easy-api.json` and runtime state on the server.
4. Run Python compilation.
5. `systemctl restart sing-box-admin`.
6. Check panel HTTP status and service status.

Runtime version `2.2.0` is published as tag `v2.2.0` and a GitHub Release; older published tags are immutable. `scripts/install.sh` validates the required panel modules, static assets and `VERSION` before package/service changes, copies the complete runtime, and compiles its Python files before starting the panel. HAPP routing is bundled in `happ_server.py`, not installed by overwriting live user/configuration files. Run the updated installer only in a maintenance window: its apt/Docker/Nginx workflow is broader than a focused panel-file deployment. Test full privileged installation separately in a disposable VM.

## Server JSON Import And TrustTunnel Upstreams

`/outbounds` → «Импорт JSON» accepts, in one request and in any mix (up to 16 servers; failures are reported as `Элемент N[.M]: reason`): a sing-box outbound object, an Xray outbound, a JSON array of such objects, a **full sing-box/Xray config** (every VPN outbound in `outbounds` is imported; `direct`, `block`, `dns`, `selector`, `urltest`, `freedom` and `blackhole` are ignored silently and do not count towards the limit), a TrustTunnel profile, and `tt://` links (as strings in an array, or one or several lines of plain text). Supported protocols: VLESS (Reality/TLS, TCP/gRPC), Hysteria2, Trojan, Shadowsocks and TrustTunnel. Invalid elements are skipped with a reason; when nothing is valid, nothing changes. Every imported server joins the `vless-auto` urltest, is validated with `sing-box check` and is applied through the shared gateway/HAPP transaction; connection checks are queued afterwards.

Accepted TrustTunnel shapes: a sing-box-style object `{"type": "trusttunnel", "server": "...", "server_port": 443, "username": "...", "password": "<password>", "tls": {"server_name": "...", "insecure": false, "certificate": ["PEM lines"]}, "upstream_protocol": "http2|http3", "tls_profile": "chrome", "anti_dpi": false, "client_random": "", "has_ipv6": true, "addresses": ["extra host:port"]}`; the official client configuration converted to JSON (`{"endpoint": {"hostname": ..., "addresses": [...], "username": ..., "password": ...}}`, foreign sections such as `listener`, `exclusions` or `killswitch_enabled` are ignored); and the `tt://?...` deep link exported by an endpoint or an AdGuard app (hostname, addresses, credentials, certificate chain, protocol, anti-DPI, client random, name). Unknown fields of the sing-box-style object are rejected instead of being ignored; credentials are limited to printable ASCII without spaces.

sing-box has no TrustTunnel outbound, so each TrustTunnel server runs through the official client (`/opt/trusttunnel/trusttunnel_client`, pinned v1.1.11, installed by `scripts/install.sh` after SHA-256 verification) as the hardened systemd instance `trusttunnel-client@<tag>.service` (user `sing-box`, no capabilities; `AF_NETLINK` is allowed because the client reads the routing table and watches network changes through it) with a loopback SOCKS5 listener on `127.0.0.1:19500-19599` protected by a random per-profile username/password. The server list, route selection, `urltest`, checks, failover and the HAPP configuration see an ordinary `socks` outbound, while the table, the edit dialog and exports show type `trusttunnel`, the real endpoint and the profile JSON (including the password, like every other server type). `/etc/sing-box/trusttunnel-clients/<tag>.toml` (`0640 root:sing-box`) and the registry `/etc/sing-box-admin/trusttunnel-servers.json` (`0600`) hold the credentials. The SOCKS password is derived from the profile, so changing a profile also changes the outbound and invalidates stale check results.

Safety properties: the generated client config always sets `killswitch_enabled = true`, `vpn_mode = "general"` and `exclusions = []`. A probe on `.39` showed that with the kill switch off the client silently falls back to a direct connection while the endpoint is unreachable, which would expose the server's own address; with it on, connections fail closed. An import starts the clients first (and aborts on a start failure), then applies the configuration, and rolls the clients back when the apply fails. Deleting or replacing a TrustTunnel server removes the orphaned client after a successful apply, and the panel reconciles clients at startup. Importing TrustTunnel reports a clear error when the client binary is missing; other protocols are unaffected. Direct import of a plain `socks` outbound is refused. Never import a TrustTunnel profile that points to this host's own HAPP TrustTunnel endpoint: choosing it as the route would create a routing loop.

Regression tests: `py -3 scripts/test_trusttunnel_upstream.py` (every input shape, deep links, TOML rendering, client lifecycle and rollback, mixed-protocol and full-config imports, panel rendering, HAPP/monitor integration).

## Firewall deployment

The libexec scripts are paired with units in `server/systemd/`. `focusvpn-gateway-mode@.service` switches between VLESS/TProxy and the system's default gateway. Default mode stops sing-box TPROXY, keeps the main default route and HAPP configuration unchanged, and sends forwarded WireGuard-server client traffic through subnet-scoped default-device FORWARD/MASQUERADE rules. Before changing mode it verifies the main route, IPv4 forwarding, return-path FORWARD and subnet MASQUERADE. The existing `wg_lan_deny` policy remains active. VLESS mode keeps the RU/private split. Default mode is not a VPN tunnel and does not encrypt traffic beyond the server. HAPP clients remain on their separate configured outbound. Keep the current ruleset backup for rollback.

The selected VLESS/default mode is restored by `focusvpn-gateway-mode.service` after reboot. Removed external mode values are not accepted by the panel or command-line controller.

## VLESS Automation

Settings provides scheduled VLESS checks with a 1-60 minute interval between completed cycles, a separate automatic-route switch, and Mattermost notifications. New installations default to disabled monitoring/auto-switch and a five-minute interval; enabling checks starts the first cycle immediately. Profiles are tested sequentially in the existing background queue, not in the HTTP request.

The displayed ping is HTTPS time to first byte through the VLESS tunnel, including tunnel setup, not ICMP to the provider IP. Results and timestamps are retained in `/etc/sing-box-admin/outbound-checks/`; failed profiles are red. Only successful VLESS profiles with measured latency participate in auto-selection. The same profile must win three consecutive complete cycles before a switch. Ties prefer the current route. Failures, manual changes, settings/profile edits and service restarts reset the streak; automatic changes are suppressed outside VLESS mode.

Successful changes use the shared gateway/HAPP apply-and-rollback transaction. The private journal `/etc/sing-box-admin/gateway-switches.jsonl` retains the latest 500 events; `/gateway-journal` shows the latest 100 under the panel authentication boundary. Settings and state are stored at `/etc/sing-box-admin/vless-monitor-settings.json` and `/etc/sing-box-admin/vless-monitor-state.json` with mode `0600`.

### Gateway availability failover

The separate setting "Автопроверка доступности VLESS" (`failover_enabled`, off by default) is independent of scheduled latency checks and automatic selection, but uses the same interval. Each cycle probes only the current default route (any server type: VLESS, Hysteria2, Trojan, Shadowsocks; `urltest` selectors are skipped because sing-box balances them itself). A switch restarts sing-box and the HAPP server, so a single failed probe is never enough: probes on live hosts fail transiently (observed on `.39`: two consecutive failures of a working Trojan gateway within 30 seconds). The first failure puts the gateway into `suspect` (1/3) and the monitor rechecks every 60 seconds (the scheduled VLESS selection cycle is skipped meanwhile, so its three-win streak is not accelerated); any success resets the counter. After three consecutive failed checks (about three minutes) the gateway is declared unavailable. Only fresh results count: `checked_at` must not be earlier than the probe start, so a stale result or a broken probe unit resets the counter instead of failing the gateway. The monitor then probes all other servers, excludes failed ones and switches to the fastest by HTTPS latency through the same gateway/HAPP apply-and-rollback transaction with source `failover`. While the gateway stays down, every interval repeats the candidate search without waiting for new failures; if no server answers, the current gateway is kept and `failover_no_candidate` is journaled once until the state changes. Failover pauses outside VLESS mode and stops if settings or the server list change mid-cycle.

Journal events: `gateway_check_failed` (1/3, 2/3), `gateway_down`, `failover_no_candidate`, `route_changed` (source `failover`), `switch_failed`. Settings shows the last gateway state (`/outbounds/checks` exposes it as `automation.gateway_text`).

### Mattermost message

The message is a template stored as `message_template` with a `utc_offset` (default `+03:00`, `-12:00..+14:00` in quarter-hour steps; the server clock is UTC). The default text is:

```
📢 VPN-шлюз: обновление статуса
📅 {date} | 🕐 {time}
🔁 Произошла смена шлюза:
➡️ Было: {old}
✅ Стало: {new} ({source})
```

Only `{date}` (DD.MM.YYYY), `{time}` (HH:MM), `{old}`, `{new}`, `{source}` and `{latency}` are substituted by a plain whitelist replacement (no format-string evaluation); unknown `{names}` are rejected on save and the text is limited to 1000 characters. An empty field restores the default. `{source}` is a label (`вручную`, `автовыбор`, `недоступность шлюза`, `смена режима`, `test`); the test-webhook button renders the same template with `{old}` = `{new}` = the current default and source `test`. Notifications still require the "Уведомлять Mattermost" switch.

Enter the Mattermost incoming-webhook URL in Settings; the URL is never displayed back or included in journal/API output. Delivery is asynchronous with a bounded timeout, and failures are journaled without undoing the route change. The test-webhook button verifies delivery using the same queue. Run `py -3 scripts/test_vless_monitor.py` and `py -3 scripts/test_route_sync.py` before deploying changes to this feature.

## HAPP Personal Access

The existing VIP URI and inbound users are snapshotted into `/etc/sing-box-admin/happ-vip.json` mode `0600` without rewriting `/etc/sing-box-admin/happ-server.json`. The VIP UUID, flow, listener, transport and TLS settings are protected against accidental replacement in panel configuration changes. The saved VIP URI is shown read-only in Settings and continues to use TCP `9445`.

Personal users are managed on `/happ-server`, with independent UUIDs, links/QR, enable/disable/delete, and optional expiration timestamps in UTC. `/etc/sing-box-admin/happ-users.json` stores the private registry; `/etc/sing-box-admin/happ-user-events.json` retains the latest 500 management events. Expiration is reconciled once per minute, so an expired credential can remain active for up to the reconciliation interval plus apply time. Existing active sessions are interrupted when the HAPP configuration is restarted; this does not revoke the VIP credential. The registry is restored if configuration application fails.

Clash API connection metadata does not directly identify users. The live view correlates its source IP, source port and start time with the authenticated VLESS journal request ID under the current HAPP PID. Binary/ANSI journal messages and Go RFC3339 nanosecond timestamps are normalized; ambiguous or missing identities are shown as `Не определён`, never assigned by IP alone. VIP is a separate shared-access group.

The live connection table groups matching client IPs by protocol. Connection duration and download/upload counters are summed; destination follows the most recently started live connection. Usernames are correlated from the authenticated VLESS journal, never by IP alone. Run `py -3 scripts/test_happ_stats.py` and `node scripts/test_happ_stats_ui.js` for identity/counter regressions, and `py -3 scripts/test_happ_users.py` for VIP-preservation and access lifecycle.

## Persistent HAPP Statistics

`/mnt/stat/happ-stat.sqlite3` stores observed HAPP connections and authenticated journal visits. The directory is mode `0700`, database/sidecar files mode `0600`; all persistent history settings and collector checkpoints live in the same database. The admin service sandbox permits writing `/mnt/stat`, and the installer creates the directory and installs `python3-xlwt` for BIFF XLS export.

A background collector runs independently of browser polling, with a two-second pause between completed samples. Stable journal/live identifiers deduplicate snapshots; traffic is the maximum observed counter per connection, not a sum of repeated cumulative snapshots. Late user identification merges the prior live record. Closed connections remain stored, and journal-only visits preserve domain/IP, source and time with unknown final bytes. Reconnects/restarts and gaps can lose unsampled final traffic; do not treat observed totals as exact billing counters. Full HTTPS URL paths and page contents are unavailable.

Separate `user_traffic_totals` in `/mnt/stat/happ-stat.sqlite3` accumulates only new per-connection byte deltas and is not pruned by history retention. Its first initialization backfills the history rows still present in SQLite; traffic deleted by an earlier retention cleanup cannot be recovered. The per-profile `Сбросить` action deletes only that user's cumulative total; active session counters remain as baselines so only subsequent byte deltas are added again.

`/happ-history` provides user/date filters, UTC timestamps, source IP/port, destination domain/IP/port, protocol and observed bytes with pagination. `/happ-history.xls` exports all matching rows as actual XLS, splitting into additional sheets at the BIFF row limit; no server-side XLS archives are retained. `/happ-history.xml` exports the same rows and filters as `application/xml; charset=utf-8`: a `<happ-statistics>` root (`exported-at`, `timezone="UTC"`, `records`, optional `user`/`since`/`until`) with one `<connection>` element per row (`started-at`, `last-seen-at`, `user-key`, `user`, `source-ip`, `source-port`, `destination`, `network`, `download-bytes`, `upload-bytes`, `status`, `accuracy`; byte attributes are omitted when final counters are unknown). Values are XML-escaped and characters illegal in XML 1.0 are stripped. All three endpoints require the normal private-network session authentication. Strings are written as XLS text, not formulas.

`Полный сброс статистики` on `/happ-history` opens a confirmation dialog and posts to `/happ-history/reset` (session, CSRF and `confirm=reset-all` are required; any other request is rejected with an error banner and nothing is deleted). It removes every history row, all cumulative user totals and all rate samples/counters, then compacts the SQLite file. Retention and collector settings, users, keys and subscriptions are kept. Connections open at the moment of the reset get a baseline in `traffic_reset_baselines` (composed from the stored bytes and any earlier baseline, so repeated resets never re-count old bytes) and are counted from zero afterwards. Export XLS/XML first if the data is still needed; the reset is irreversible and external backups keep their own copies.

Settings controls retention from 1 to 3650 days, default 60. Lowering retention immediately deletes records whose last observation is older than the cutoff; automatic cleanup runs at startup and hourly. SQLite secure deletion and vacuum reclaim deleted records; external filesystem snapshots, manual backups and downloaded XLS files require independent retention policies. A collector error preserves prior history and is visible on the history page.

Local regression tests require `xlwt==1.3.0`: `py -3 -m pip install xlwt==1.3.0`, then `py -3 scripts/test_happ_history.py`. Runtime secrets/credentials are never copied into this history database or repository.

## HAPP Account Traffic And Subscriptions

The personal user row and TOP-5 show cumulative observed download/upload after Open HAPP, including closed sessions. Browser polling refreshes these account totals independently from active-connection totals. Lifetime totals survive history retention cleanup and can be reset for one profile from its row. The reset clears that profile's lifetime totals, `/happ-history` rows and rate samples (other profiles are unaffected); the counters of currently open sessions become the new baseline (`traffic_reset_baselines`, purged by the retention cleanup) so bytes accumulated before the reset are not counted again. Existing retained rows are backfilled on first initialization; previously pruned data is unavailable. They are sampled lower bounds, not exact billing or enforced quotas.

Open HAPP and Copy subscription use an account-specific HTTP subscription. VIP and personal QR codes now encode that same WAN/DNS subscription URL for mobile import, not a standalone VLESS URI. Scan inside HAPP to import the subscription and receive metadata. The original VLESS copy remains unchanged. Previously imported standalone configurations must be replaced or supplemented by importing the new subscription; they are not converted automatically. Responses carry `subscription-userinfo: upload=...; download=...; total=0`, optional UTC expiry, `profile-title`, and `profile-update-interval` (hours; default `1`, see Settings below), also represented as metadata lines in the body. HAPP's standard usage bar shows upload plus download against an unlimited allowance, not download alone. Automatic refresh is requested at the configured interval (default hourly); execution depends on the client, and manual refresh retrieves current collected counters.

Subscriptions also contain a `happ://routing/onadd/<Base64 JSON>` line with the stable profile name `FocusVPN Direct`, `GlobalProxy: "true"`, and the explicit `domain:` exceptions in `HAPP_DIRECT_SITES` in `server/panel/happ_server.py`. HAPP routes those domains and their subdomains directly through the client's normal Wi-Fi/mobile connection; unmatched traffic continues through the selected VPN. The routing link is sent in the body, not a large HTTP header, to avoid exceeding reverse-proxy response-header buffers. No server outbound, UUID, account token, subscription URL, or runtime user state is changed. Existing sessions remain connected: deploy only the generator and restart `sing-box-admin`, not either VPN service. Existing subscription clients receive the profile on their next update and apply it after reconnecting; standalone VLESS imports stay valid but do not receive these rules automatically. Re-imports update the same named profile instead of creating duplicates. IP-check sites in the exceptions, including `api.ipify.org` and `ifconfig.me`, intentionally show the client's real public IP. Validate actual import and direct egress in HAPP on a client device after deployment; server-side tests alone do not prove client application of the rules.

The profile carries the stable revision `LastUpdated: "1791417601"` (2026-10-08 UTC); increase it only when changing routing, not on every subscription request. It now has 201 exceptions, including `domain:focuslens.dev` for the domain and all its subdomains, routed through the client's own LAN/WAN connection. In Windows HAPP 4.3.0.610, the initial body-only profile was confirmed in the local routing store with all 200 original domains, enabled and selected for the FocusVPN subscription. The running Xray started at 00:01:23, before the profile's geo files finished loading at 00:02:28. Subscription refresh does not restart the running core, and the original VLESS-derived server JSON can stay unchanged because routing is stored separately. Wait until the routing profile has no geo-file errors, then disconnect/reconnect HAPP to rebuild the active core configuration. Do not rotate account credentials or restart server VPN services to fix a stale client session. A domain such as `mc.yandex.ru` is already covered by `domain:yandex.ru`; cached analytics reports are not a substitute for checking a new request after reconnecting. Client reconnect and actual direct egress still require verification on the device.

The subscription title starts with the network emoji U+1F5A7 followed by `FocusVPN` and the user name. UTF-8 Base64 metadata preserves it for both copied subscriptions and Open HAPP, including subsequent refreshes; the existing 25-character title limit remains.

The subscription sends the private-team notice through the standard `announce: base64:...` header and matching body metadata. Settings → «Заголовок и объявление HAPP» lets administrators change the server title and announcement. The saved title also becomes the panel's HTML `<title>` on the next page request, without a service restart; HTML markup is escaped and unavailable/invalid state falls back to the default title. A final announcement line displays both observed account counters, for example `DL: 512.0 MB / UL: 1.0 GB`, using B/KB/MB/GB/TB with base-1024 scaling and `0 B` for zero. Both values are recalculated on each subscription request. Standard `subscription-userinfo` still carries both real byte counters, and HAPP's native usage bar still sums upload plus download. The default announcement fits the documented 200-character limit including UTF-16 surrogate pairs; custom text rendering limits remain client-controlled. This uses standard mobile HAPP metadata, not a desktop-only setting; standalone VLESS and older VLESS-only QR imports do not receive subscription metadata. Manual refresh fetches current collected counters, and automatic refresh is requested at the configured interval (default hourly).

The same Settings block has «Время обновления подписки, минут» (10–600, default 60, stored as `subscription_update_minutes` in `/etc/sing-box-admin/happ-server.json`). HAPP documents `profile-update-interval` as an integer number of hours ("must be a multiple of one hour"), so the panel sends the nearest whole number of hours, at least 1: 10–89 min → `1`, 90–149 → `2`, …, 600 → `10`; the form shows the value actually sent. Values outside 10–600 or non-integers are rejected on save and leave the file unchanged; a corrupt stored value falls back to 60 minutes instead of breaking subscriptions. The client applies a new interval at its next subscription update (on Windows, `%LOCALAPPDATA%\Happ\logs\subscription_log.txt` shows `global interval: N hour(s)` and `N servers imported/updated` after each update).

`profile-web-page-url` links to `/happ-info`, a nonsecret informational page with justified paragraphs and responsive wrapping. The anonymous page shows only the shared notice, not account statistics. HAPP's native announcement alignment is controlled by the client; subscriptions cannot enforce CSS justification inside that field. The information page follows the configured network filter and does not grant access to admin pages.

Subscription origin priority is `subscription_base_url` in HAPP Public link JSON (editable through the dedicated public-subscription URL field in Settings), then `FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL`, then `https://<HAPP public server IP or DNS>:7445`. The automatic URL uses the installer's external HTTPS proxy, not its loopback-only HTTP backend on port 9443. There is no hardcoded LAN fallback. Clearing the Settings override restores automatic public-host selection; an explicit HTTPS URL can specify a different external port or reverse-proxy path. Copy subscription, Open HAPP, mobile QR and profile information metadata share this resolver. Re-copy or re-import subscriptions saved with the old HTTP port; their account tokens remain unchanged. Clients must trust the HTTPS certificate; use a trusted certificate and reachable public origin for remote clients. Raw VLESS copy retains the existing public HAPP endpoint and port 9445. Never derive the origin from untrusted Host headers.

New standalone installations expose the authenticated IPv4 HTTPS panel on port 7445 immediately, with `FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0` and backend `127.0.0.1:9443`. Missing/empty admin ACLs receive this default; a nonempty explicit CIDR is preserved. Set a restricted IPv4 CIDR in `/etc/focusvpn/focusvpn.env` and restart `sing-box-admin` to limit access. The app without installer-provided settings defaults to LAN/VPN/management networks. `FOCUSVPN_ADMIN_NETWORK` adds an IPv4 access network to both application and installer-rendered nftables rules. Authentication, per-account tokens, port 51821 protection and individual WireGuard LAN bans remain in effect.

On the historical LAN/proxy deployment, direct WAN access to `37.208.69.6:9443` was closed at the perimeter and externally observed as a TCP timeout; verify the router/firewall rule if it changes. That backend listens on HTTP at `192.168.0.39:9443` for the gateway Nginx proxy and remains reachable to LAN peers unless separately restricted at the host firewall.

On gateway `192.168.0.15`, the Nginx vhost config `server/config/nginx/vpn.focuslens.dev` serves `https://vpn.focuslens.dev` and reverse-proxies to `192.168.0.39:9443`. Exactly one `server_name vpn.focuslens.dev` TLS vhost is active; stale Certbot maintenance catch-all blocks were removed. `FOCUSVPN_TRUSTED_PROXY_NETWORKS=192.168.0.15/32` on the VPN host binds sessions to the forwarded WAN client IP, rejects spoofed forwarding headers from other peers, and marks HTTPS session cookies Secure. Set HAPP `subscription_base_url` to `https://vpn.focuslens.dev`; it can be changed later through Settings. Direct WAN mapping for `37.208.69.6:9443` is now closed; an external probe timed out, while HTTPS on the domain succeeds. The backend still listens on plaintext HTTP for the private proxy link. Because the application allowlist remains `0.0.0.0/0` by explicit request, LAN clients with direct reachability to `192.168.0.39:9443` can still bypass TLS; restrict the host firewall to proxy `192.168.0.15` if TLS must also be enforced for LAN clients.

`/happ-subscription/<token>` requires a secret per-account HMAC token instead of an admin session and returns only that account's VLESS configuration and counters. The master key is created once in `/etc/sing-box-admin/happ-subscription-key.json`, mode `0600`; keep it across restarts/backups to preserve links. Rotating it revokes every subscription URL. Disabled, expired, deleted and invalid tokens return 404. VIP remains a shared account; its original credentials are unchanged. Run `py -3 scripts/test_happ_users.py`, `py -3 scripts/test_happ_history.py`, and `node scripts/test_happ_stats_ui.js` for subscription isolation, revocation, metadata and UI regressions.

## HAPP Protocols: Trojan, Hysteria2 And TrustTunnel

Settings → «Протоколы HAPP: Trojan, Hysteria2 и TrustTunnel» (`POST /settings/happ/protocols`) adds three optional access protocols next to the VLESS Reality inbound. All are off by default. Saving runs the usual HAPP transaction (candidate config check, restart of `sing-box-happ-server`, rollback of settings, configuration and TrustTunnel state on failure); the VIP credentials and the VLESS inbound are not changed, and active HAPP sessions reconnect once. Settings live in `/etc/sing-box-admin/happ-protocols.json` (mode `0600`; a file saved before Hysteria2 existed still loads, Hysteria2 simply stays off on its default port). Every personal user gets three extra random secrets, `trojan_password`, `tt_password` and `hy2_password`, in `happ-users.json`: they are backfilled once for existing users on panel start (only the missing ones) and created with new users. VIP does not receive these protocols. A disabled, expired or deleted user loses access to all protocols (Trojan and Hysteria2 user lists and TrustTunnel credentials are rebuilt from the active users).

- **Hysteria2** is the inbound `happ-hysteria2-in` (default UDP `9448`, QUIC) inside the existing HAPP sing-box, using the same TLS certificate as Trojan; every active user is a `personal-<id>` Hysteria2 user with its own password and `ignore_client_bandwidth` is on (the server picks BBR instead of trusting the bandwidth a client declares). The link `hysteria2://<password>@<host>:<port>/?sni=<name>&insecure=1&pinSHA256=<sha256-hex>#<user> Hysteria2` is appended to the HAPP subscription after the Trojan link (HAPP supports Hysteria2) and offered as «Hysteria2»/«Hysteria2 QR» on `/happ-server`; with a custom certificate only `sni` is sent. The router must forward UDP `9448` (or the chosen port) to the server. The journal-based statistics attribute traffic through `inbound/hysteria2[happ-hysteria2-in]` lines. The link format and the inbound were verified on `.39` in a throw-away sandbox with the sing-box client and the official Hysteria2 client v2.13.0 (correct pin and password connect, wrong pin or password are rejected). Whether a given HAPP build honours `pinSHA256` for a self-signed certificate is client behaviour; if it does not, use a certificate from a trusted CA.
- **Trojan** is the inbound `happ-trojan-in` (default TCP `9446`) inside the existing HAPP sing-box, TLS with the configured certificate; every active user is a `personal-<id>` Trojan user, so the journal-based statistics attribute traffic exactly as for VLESS. The link `trojan://<password>@<host>:<port>?security=tls&sni=<name>&fp=chrome&type=tcp#<user> Trojan` is appended to the user's HAPP subscription after the VLESS link (HAPP supports Trojan) and offered as «Trojan»/«Trojan QR» on `/happ-server`. Standard Trojan clients and Trojan-Go clients in plain TLS mode can use it; Trojan-Go WebSocket/mux extensions are not configured.
- **TrustTunnel** (AdGuard) carries tunnels over HTTP/2 and HTTP/3 (QUIC) as independent streams, so one lost packet does not stall the others (no head-of-line blocking of a single TCP connection). The endpoint `/opt/trusttunnel/trusttunnel_endpoint` (pinned v1.1.0, installed by `scripts/install.sh` after SHA-256 verification against the release metadata; a failed download only leaves the protocol unavailable) runs as the separate hardened unit `trusttunnel.service` (user `sing-box`, default TCP+UDP `9447`). Its files are in `/etc/sing-box-happ-server/trusttunnel/` (`vpn.toml`, `hosts.toml`, `credentials.toml`; directory `0750`, files `0640 root:sing-box`). Traffic is forwarded to the loopback SOCKS inbound `happ-tt-socks-in` (`127.0.0.1:19448`) of the HAPP sing-box and therefore follows the same provider route as other HAPP clients. TrustTunnel reads credentials only at start, so the panel restarts the unit whenever the set of active users changes and re-checks it every minute (a stopped unit is started again). Traffic arrives through an anonymous SOCKS bridge and is not attributed per user. HAPP cannot import TrustTunnel: the `tt://` deep link (copy and QR on `/happ-server`) is for the TrustTunnel apps or CLI client.
- **TLS** defaults to a self-signed ECDSA P-256 certificate created by the panel with OpenSSL (`/etc/sing-box-happ-server/tls/focusvpn-protocols.{crt,key}`, valid 10 years, SAN = TLS name or IP). It is pinned in the links: the TrustTunnel link embeds the certificate, the Trojan link carries `allowInsecure=1` and `pcs=<SHA-256>`, the Hysteria2 link carries `insecure=1` and `pinSHA256=<SHA-256>`. Some clients ignore the pin; for them, or for a production deployment with a domain, enter the paths of a certificate chain and key from a trusted CA (the files must be readable by group `sing-box`; re-apply the settings after each renewal). «Создать сертификат заново» replaces the generated certificate, so every Trojan, Hysteria2 and TrustTunnel link must be imported again.
- **Ports** must be 1024–65535 because neither service has the privilege to bind low ports; reserved panel/VPN ports are rejected. The defaults are Trojan `9446/tcp`, Hysteria2 `9448/udp` and TrustTunnel `9447/tcp+udp`; the three ports must differ. Forward them on the router; to present 443 to clients, forward external 443 to the chosen port. The public address and TLS name in links default to the subscription host and can be overridden in Settings.
- **Statistics** parser accepts `inbound/trojan[happ-trojan-in]` and `inbound/hysteria2[happ-hysteria2-in]` journal lines next to the VLESS inbound.

Verify after a change with `systemctl status trusttunnel`, `ss -tlnp`/`ss -ulnp` for the three ports and `journalctl -u trusttunnel -u sing-box-happ-server`. To retire TrustTunnel, disable it in Settings (the panel stops and disables the unit and removes `credentials.toml`). Regression tests: `py -3 scripts/test_happ_protocols.py` (settings validation, golden `tt://` deep link byte-identical to the real endpoint export, transactional apply/rollback, lifecycle sync, installer pinning).

## CRM Administration Bridge

The CRM administration module uses the existing panel instead of installing a second VPN gateway. Service-token mode requires `FOCUSVPN_CRM_TOKEN_FILE` and an explicit `FOCUSVPN_CRM_NETWORKS` allowlist. Administrator-password mode requires valid panel credentials plus `X-FocusVPN-CRM: 1` on server-side requests; network admission and normal session login remain unchanged.

Install the optional `server/systemd/sing-box-admin-crm-bridge.conf` drop-in on the VPN host and the matching CRM drop-in. Store a dedicated shared 32-byte random token in the referenced root-only files with mode `0600`. Never reuse a panel password or publish the token. The example endpoints use the private LAN; use verified TLS for an untrusted link.

CRM forwards only the server-side service authorization, not a browser cookie or incoming authorization header. Every proxied route requires an active CRM principal with `admin` or `architect`. The VPN panel keeps its CSRF fields and normal apply/rollback behavior. Embedded navigation, polling, QR and downloads use `/admin/vpn/panel/`; public subscription URLs remain unchanged.

The CRM connection editor is at `/admin?tab=settings`. Addresses and encrypted administrator credentials live in CRM's private `CRM_VPN_SETTINGS_FILE`, not in browser storage; server changes take effect without a CRM restart. Use the read-only connection check before switching hosts. Use verified HTTPS for a routed or public network. Shared-token mode still needs the token and source allowlist on the destination; password mode needs the compatible panel code and valid administrator credentials.

History retention is saved by `POST /settings/happ-history` in the settings handler. Successful saves and validation failures redirect to `/settings`; changing retention can purge expired rows under the selected policy. The HTTP regression uses a temporary SQLite database and verifies persistence, invalid days and CSRF rejection.

Only `sing-box-admin` needs restarting when installing the bridge. Do not reinstall WireGuard or overwrite runtime configuration, authentication, client keys or statistics. Test with `py -3 scripts/test_crm_bridge.py`, `node scripts/test_panel_checks.js` and `node scripts/test_happ_stats_ui.js`.

## HAPP Activity Metrics

`/happ-server` shows per-user connection state, source IPs, connection counts, recorded lifetime Download/Upload totals, current rates and peak rates in a selectable 1-60 second trailing window. Rates are counter deltas measured by the background collector on a nominal one-second cycle, not instantaneous link capacity. A disconnected VPN client cannot be detected until it has an observable connection; idle connected rows mean open connections without measured traffic.

The history chart switches between Download and Upload and retains the existing 10/30/60/90 minute ranges and top-10 peak ranking. The window moves continuously from right to left on the server clock (about ten redraws per second while the tab is visible; disabled when the browser requests reduced motion) and each live poll appends new points without reloading the history. Every user keeps a distinct line colour, the peak inside the visible window is marked with a point and a speed label, and the legend is recomputed from the visible peaks (sorted by peak, bar length relative to the maximum), so values and order change as peaks scroll out of the window. Upload-rate history starts after this deployment; old rows remain unknown, not reconstructed. Lifetime totals survive retention cleanup and can still be reset per user (the reset also clears that user's history rows and rate samples). Journal-only visits without counters are marked separately; bytes missed between samples cannot be recovered from those log entries. Collector errors or samples older than ten seconds clear live states instead of displaying stale rates as current.

Deployment adds nullable Upload columns to the existing SQLite sample/counter tables. Back up `/mnt/stat/happ-stat.sqlite3` with the SQLite backup API before restarting `sing-box-admin`; no VPN service restart or runtime credential change is required.

## Rollback

For the HAPP activity migration, stop `sing-box-admin` and restore both the previous panel files and the pre-migration SQLite backup before starting it again. The previous collector uses positional inserts incompatible with the added columns. Restoring that snapshot discards statistics collected after the backup; retain a fresh copy of the current database before rolling back.

Use the timestamped backup created on the server, restore the specific file, run its syntax check, then restart only the owning service. Never use a blanket reset or overwrite unrelated runtime state.
