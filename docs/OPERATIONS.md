# Operations

## Production paths

```text
/opt/sing-box-admin/
/etc/sing-box/config.json
/etc/sing-box-happ-server/config.json
/etc/sing-box-admin/
/etc/wireguard/wg-client.conf
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

## VPN Host Metrics

Settings shows the VPN host LAN address and OS, uptime, rolling 24-hour CPU/LAN peaks, and OS RX/TX byte counters since boot. The panel samples `/proc/stat`, `/proc/net/dev`, and `/proc/uptime` every five seconds; physical interfaces are preferred, with virtual interfaces excluded. Samples are stored in `/mnt/stat/server-metrics.sqlite3` (directory mode `0700`, database mode `0600`) and pruned after 24 hours. CPU/LAN peak history begins when this collector is installed; RX/TX totals reset when the operating system reboots. These are host metrics, not billing counters.

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
curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/v2.1.4/scripts/bootstrap.sh | sudo bash -s -- v2.1.4
```

До любых изменений `bootstrap.sh` показывает пакеты и службы и требует явного подтверждения. Если Git или системные пакеты отсутствуют, они устанавливаются через apt только после согласия; далее bootstrap клонирует выбранный ref и запускает тот же installer. Ввод паролей и подтверждения передаётся на управляющий TTY даже при `curl | sudo bash`. Пароль панели подтверждается, хранится только как salted scrypt hash с правами `0600` и не передаётся через argv/environment. Installer проверяет sing-box по SHA-256 digest точного asset из GitHub Release API, настраивает Nginx HTTPS `:7445`, запускает панель и wg-easy автоматически. Setup API wg-easy доступен только на localhost: используйте `ssh -L 51821:127.0.0.1:51821 root@<server-ip>`, затем откройте `http://127.0.0.1:51821`. При старых WireGuard units/container/configs показывается отдельное предупреждение; согласие перемещает конфиги в приватный backup, отказ отменяет установку без очистки. Остальные VPN-службы запускаются только с `--enable` после настройки реальных конфигураций.

For a clean-clone smoke without changing a server, clone the same public ref into a temporary directory, check that `VERSION`, `scripts/bootstrap.sh`, `scripts/install.sh`, panel/helper files and packaged systemd units exist, run the Python/Node/publication suites from that checkout, and invoke `scripts/install.sh --help`. A full OS/service startup test requires a disposable Debian/Ubuntu VM; unit tests on Windows do not emulate apt/systemd or start VPN networking.

For the published v2.1.4 Git ref on Windows/PowerShell, run the smoke from a fresh clone:

```powershell
$clone = Join-Path $env:TEMP ('FocusVPN-v2.1.4-' + [guid]::NewGuid().ToString('N'))
git clone --depth 1 --branch v2.1.4 https://github.com/DgekStr/FocusVPN.git $clone
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
curl -fsSL https://raw.githubusercontent.com/DgekStr/FocusVPN/v2.1.4/scripts/bootstrap.sh | sudo bash -s -- v2.1.4
```

`bootstrap.sh` accepts a Git branch/tag followed by installer options, for example `v2.1.1 --start-wg-easy`; if packages need installation it downloads them through apt, clones the requested ref into a temporary directory, and runs the same installer. `scripts/create_panel_auth.py` prompts through the controlling TTY (so the command also works when bootstrap is piped), confirms a 12+ character panel password, stores only a salted scrypt hash atomically with mode `0600`, and never sends the password through argv/environment. The installer writes all service unit files and verifies sing-box against the official GitHub Release API asset digest, but does not start VPN services with placeholder configs. After entering real gateway/HAPP/wg-easy configs, run `sudo ./scripts/install.sh --enable` from a clone, or repeat the standalone bootstrap with `--enable`. wg-easy initial setup is optional and separately requested with `--start-wg-easy`; its admin/API configuration must be completed before `--enable`.

To verify the clone/deploy path without touching the production host, run `git clone --branch <ref> --depth 1 https://github.com/DgekStr/FocusVPN.git <temporary-dir>`, then execute `scripts/validate.ps1` and the documented Python/Node suites from that checkout. The bootstrap uses only the cloned repository; production runtime credentials are not sourced from Git.

The default outbound and server profiles are shared between the gateway and HAPP configuration. Applying a server or route change validates and backs up both files, then restarts both services in VLESS mode. In WireGuard mode HAPP retains its marked direct outbound; the chosen provider route is stored for the return to VLESS. Regression check: `py -3 scripts/test_route_sync.py`.

Server JSON import queues `focusvpn-outbound-test@<tag>.service` checks after saving validated profiles and returns without waiting for network tests. An isolated sing-box process with a loopback SOCKS listener checks HTTPS egress without switching the production route or holding the configuration lock. The manager polls the authenticated `/outbounds/checks` endpoint and displays queued, running, success or error status per profile. Only a sanitized result is stored under `/etc/sing-box-admin/outbound-checks/`. The manager provides a repeat-check button; TLS verification stays enabled unless explicitly configured otherwise in the imported profile.

1. Copy `server/panel/*.py` to `/opt/sing-box-admin/`.
2. Copy `server/panel/static/` to `/opt/sing-box-admin/static/`.
3. Keep root-only files `auth.json`, `wg-easy-api.json` and runtime state on the server.
4. Run Python compilation.
5. `systemctl restart sing-box-admin`.
6. Check panel HTTP status and service status.

## Firewall deployment

The libexec scripts are paired with units in `server/systemd/`. `focusvpn-gateway-mode@.service` switches among VLESS/TProxy, the system's default gateway, and external `wg-client`. In default mode it stops sing-box TPROXY and external WG, keeps the main default route unchanged, restores HAPP's normal provider outbound, and sends forwarded WireGuard-client Internet traffic through the existing default-device FORWARD/MASQUERADE rules. Before changing mode it verifies the main default route, the post-transition route decision for a `wg0` client, IPv4 forwarding, return-path FORWARD, and subnet MASQUERADE; it refuses the transition if any prerequisite is absent. The existing `wg_lan_deny` policy remains active. VLESS mode keeps the RU/private split; external WG mode remains source-policy scoped to `FOCUSVPN_WG_NETWORK`; `FOCUSVPN_LAN_NETWORK` uses the main route table in tunnel modes. Default mode provides ordinary gateway egress without TPROXY; this is not a VPN tunnel and does not encrypt traffic beyond the server. The mode is for the WireGuard client subnet; HAPP inbound clients remain on their separate configured HAPP outbound. Keep the current ruleset backup for rollback.

The external peer configuration is entered in the authenticated Settings page and stored root-only at `/etc/wireguard/wg-client.conf`. Use a single peer with IPv4 `AllowedIPs = 0.0.0.0/0`; do not add `PostUp`, `PreUp`, or other shell hooks. The selected mode is restored by `focusvpn-gateway-mode.service` after reboot. Do not switch to WireGuard until the external peer is provisioned and reachable.

## VLESS Automation

Settings provides scheduled VLESS checks with a 1-60 minute interval between completed cycles, a separate automatic-route switch, and Mattermost notifications. New installations default to disabled monitoring/auto-switch and a five-minute interval; enabling checks starts the first cycle immediately. Profiles are tested sequentially in the existing background queue, not in the HTTP request.

The displayed ping is HTTPS time to first byte through the VLESS tunnel, including tunnel setup, not ICMP to the provider IP. Results and timestamps are retained in `/etc/sing-box-admin/outbound-checks/`; failed profiles are red. Only successful VLESS profiles with measured latency participate in auto-selection. The same profile must win three consecutive complete cycles before a switch. Ties prefer the current route. Failures, manual changes, settings/profile edits and service restarts reset the streak; automatic changes are suppressed while WireGuard mode is active.

Successful changes use the shared gateway/HAPP apply-and-rollback transaction. The private journal `/etc/sing-box-admin/gateway-switches.jsonl` retains the latest 500 events; `/gateway-journal` shows the latest 100 under the panel authentication boundary. Settings and state are stored at `/etc/sing-box-admin/vless-monitor-settings.json` and `/etc/sing-box-admin/vless-monitor-state.json` with mode `0600`.

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

`/happ-history` provides user/date filters, UTC timestamps, source IP/port, destination domain/IP/port, protocol and observed bytes with pagination. `/happ-history.xls` exports all matching rows as actual XLS, splitting into additional sheets at the BIFF row limit; no server-side XLS archives are retained. Both endpoints require the normal private-network session authentication. Strings are written as XLS text, not formulas.

Settings controls retention from 1 to 3650 days, default 60. Lowering retention immediately deletes records whose last observation is older than the cutoff; automatic cleanup runs at startup and hourly. SQLite secure deletion and vacuum reclaim deleted records; external filesystem snapshots, manual backups and downloaded XLS files require independent retention policies. A collector error preserves prior history and is visible on the history page.

Local regression tests require `xlwt==1.3.0`: `py -3 -m pip install xlwt==1.3.0`, then `py -3 scripts/test_happ_history.py`. Runtime secrets/credentials are never copied into this history database or repository.

## HAPP Account Traffic And Subscriptions

The personal user row and TOP-5 show cumulative observed download/upload after Open HAPP, including closed sessions. Browser polling refreshes these account totals independently from active-connection totals. Lifetime totals survive history retention cleanup and can be reset for one profile from its row. Existing retained rows are backfilled on first initialization; previously pruned data is unavailable. They are sampled lower bounds, not exact billing or enforced quotas.

Open HAPP and Copy subscription use an account-specific HTTP subscription. VIP and personal QR codes now encode that same WAN/DNS subscription URL for mobile import, not a standalone VLESS URI. Scan inside HAPP to import the subscription and receive metadata. The original VLESS copy remains unchanged. Previously imported standalone configurations must be replaced or supplemented by importing the new subscription; they are not converted automatically. Responses carry `subscription-userinfo: upload=...; download=...; total=0`, optional UTC expiry, `profile-title`, and `profile-update-interval: 1`, also represented as metadata lines in the body. HAPP's standard usage bar shows upload plus download against an unlimited allowance, not download alone. Automatic refresh is requested hourly; execution depends on the client, and manual refresh retrieves current collected counters.

The subscription title starts with the network emoji U+1F5A7 followed by `FocusVPN` and the user name. UTF-8 Base64 metadata preserves it for both copied subscriptions and Open HAPP, including subsequent refreshes; the existing 25-character title limit remains.

The subscription sends the requested private-team notice through the standard `announce: base64:...` header and matching body metadata: `🔒Это частный VPN сервер, для работы команды разработчиков focuslens.dev. Если вы здесь оказались - это не случайно ❤️`. A second line displays account downloads as `Скачано: N.NN МБ / ∞` or `Скачано: N.NN ГБ / ∞`, switching at 1024 MiB. It is recalculated on each subscription request from retained observed download counters; upload is not included in this dedicated metric. Standard `subscription-userinfo` still carries both real byte counters, and HAPP's native usage bar still sums upload plus download. The announcement fits the documented 200-character limit including UTF-16 surrogate pairs. It works through the standard metadata mechanism used by mobile HAPP, not a desktop-only setting; standalone VLESS and older VLESS-only QR imports do not receive subscription metadata. Manual refresh fetches current collected counters, and automatic refresh remains requested hourly.

`profile-web-page-url` links to `/happ-info`, a nonsecret informational page with justified paragraphs and responsive wrapping. The anonymous page shows only the shared notice, not account statistics. HAPP's native announcement alignment is controlled by the client; subscriptions cannot enforce CSS justification inside that field. The information page follows the configured network filter and does not grant access to admin pages.

Subscription origin priority is `subscription_base_url` in HAPP Public link JSON (editable through the dedicated public-subscription URL field in Settings), then `FOCUSVPN_HAPP_SUBSCRIPTION_BASE_URL`, then `http://<HAPP public server IP or DNS>:<admin port>`. There is no hardcoded LAN fallback. Clearing the Settings override restores automatic public-host selection; an explicit HTTPS URL can specify a different external port or reverse-proxy path. Copy subscription, Open HAPP, mobile QR and profile information metadata share this resolver. Raw VLESS copy retains the existing public HAPP endpoint and port 9445. Never derive the origin from untrusted Host headers.

The panel defaults to LAN/VPN/management networks. `FOCUSVPN_ADMIN_NETWORK` adds an IPv4 access network to both application and installer-rendered nftables rules; `0.0.0.0/0` admits all IPv4 sources at the application layer. This setting remains enabled on the current server by request. Direct WAN access to `37.208.69.6:9443` was subsequently closed at the perimeter and externally observed as a TCP timeout; verify the router/firewall rule if it changes. The backend still listens on HTTP at `192.168.0.39:9443` for the gateway Nginx proxy and is reachable to LAN peers unless separately restricted at the host firewall. Authentication, per-account tokens, port 51821 protection and individual WireGuard LAN bans remain in effect.

On gateway `192.168.0.15`, the Nginx vhost config `server/config/nginx/vpn.focuslens.dev` serves `https://vpn.focuslens.dev` and reverse-proxies to `192.168.0.39:9443`. Exactly one `server_name vpn.focuslens.dev` TLS vhost is active; stale Certbot maintenance catch-all blocks were removed. `FOCUSVPN_TRUSTED_PROXY_NETWORKS=192.168.0.15/32` on the VPN host binds sessions to the forwarded WAN client IP, rejects spoofed forwarding headers from other peers, and marks HTTPS session cookies Secure. Set HAPP `subscription_base_url` to `https://vpn.focuslens.dev`; it can be changed later through Settings. Direct WAN mapping for `37.208.69.6:9443` is now closed; an external probe timed out, while HTTPS on the domain succeeds. The backend still listens on plaintext HTTP for the private proxy link. Because the application allowlist remains `0.0.0.0/0` by explicit request, LAN clients with direct reachability to `192.168.0.39:9443` can still bypass TLS; restrict the host firewall to proxy `192.168.0.15` if TLS must also be enforced for LAN clients.

`/happ-subscription/<token>` requires a secret per-account HMAC token instead of an admin session and returns only that account's VLESS configuration and counters. The master key is created once in `/etc/sing-box-admin/happ-subscription-key.json`, mode `0600`; keep it across restarts/backups to preserve links. Rotating it revokes every subscription URL. Disabled, expired, deleted and invalid tokens return 404. VIP remains a shared account; its original credentials are unchanged. Run `py -3 scripts/test_happ_users.py`, `py -3 scripts/test_happ_history.py`, and `node scripts/test_happ_stats_ui.js` for subscription isolation, revocation, metadata and UI regressions.

## CRM Administration Bridge

The CRM administration module uses the existing panel instead of installing a second VPN gateway. Service-token mode requires `FOCUSVPN_CRM_TOKEN_FILE` and an explicit `FOCUSVPN_CRM_NETWORKS` allowlist. Administrator-password mode requires valid panel credentials plus `X-FocusVPN-CRM: 1` on server-side requests; network admission and normal session login remain unchanged.

Install the optional `server/systemd/sing-box-admin-crm-bridge.conf` drop-in on the VPN host and the matching CRM drop-in. Store a dedicated shared 32-byte random token in the referenced root-only files with mode `0600`. Never reuse a panel password or publish the token. The example endpoints use the private LAN; use verified TLS for an untrusted link.

CRM forwards only the server-side service authorization, not a browser cookie or incoming authorization header. Every proxied route requires an active CRM principal with `admin` or `architect`. The VPN panel keeps its CSRF fields and normal apply/rollback behavior. Embedded navigation, polling, QR and downloads use `/admin/vpn/panel/`; public subscription URLs remain unchanged.

The CRM connection editor is at `/admin?tab=settings`. Addresses and encrypted administrator credentials live in CRM's private `CRM_VPN_SETTINGS_FILE`, not in browser storage; server changes take effect without a CRM restart. Use the read-only connection check before switching hosts. Use verified HTTPS for a routed or public network. Shared-token mode still needs the token and source allowlist on the destination; password mode needs the compatible panel code and valid administrator credentials.

History retention is saved by `POST /settings/happ-history` in the settings handler. Successful saves and validation failures redirect to `/settings`; changing retention can purge expired rows under the selected policy. The HTTP regression uses a temporary SQLite database and verifies persistence, invalid days and CSRF rejection.

Only `sing-box-admin` needs restarting when installing the bridge. Do not reinstall WireGuard or overwrite runtime configuration, authentication, client keys or statistics. Test with `py -3 scripts/test_crm_bridge.py`, `node scripts/test_panel_checks.js` and `node scripts/test_happ_stats_ui.js`.

## Rollback

Use the timestamped backup created on the server, restore the specific file, run its syntax check, then restart only the owning service. Never use a blanket reset or overwrite unrelated runtime state.
