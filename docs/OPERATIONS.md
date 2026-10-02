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

## Validation

```bash
sing-box check -C /etc/sing-box
sing-box check -C /etc/sing-box-happ-server
nft --check -f /etc/sing-box/tproxy.nft
systemd-analyze verify /etc/systemd/system/sing-box-admin.service
```

## Panel deployment

The default outbound and server profiles are shared between the gateway and HAPP configuration. Applying a server or route change validates and backs up both files, then restarts both services in VLESS mode. In WireGuard mode HAPP retains its marked direct outbound; the chosen provider route is stored for the return to VLESS. Regression check: `py -3 scripts/test_route_sync.py`.

Server JSON import queues `focusvpn-outbound-test@<tag>.service` checks after saving validated profiles and returns without waiting for network tests. An isolated sing-box process with a loopback SOCKS listener checks HTTPS egress without switching the production route or holding the configuration lock. The manager polls the authenticated `/outbounds/checks` endpoint and displays queued, running, success or error status per profile. Only a sanitized result is stored under `/etc/sing-box-admin/outbound-checks/`. The manager provides a repeat-check button; TLS verification stays enabled unless explicitly configured otherwise in the imported profile.

1. Copy `server/panel/*.py` to `/opt/sing-box-admin/`.
2. Copy `server/panel/static/` to `/opt/sing-box-admin/static/`.
3. Keep root-only files `auth.json`, `wg-easy-api.json` and runtime state on the server.
4. Run Python compilation.
5. `systemctl restart sing-box-admin`.
6. Check panel HTTP status and service status.

## Firewall deployment

The libexec scripts are paired with units in `server/systemd/`. `focusvpn-gateway-mode@.service` switches between VLESS/TProxy and the external `wg-client` interface. Routes are source-policy scoped to `FOCUSVPN_WG_NETWORK`; `FOCUSVPN_LAN_NETWORK` uses the main route table. Apply nft rules only after validation; keep the current ruleset backup for rollback.

The external peer configuration is entered in the authenticated Settings page and stored root-only at `/etc/wireguard/wg-client.conf`. Use a single peer with IPv4 `AllowedIPs = 0.0.0.0/0`; do not add `PostUp`, `PreUp`, or other shell hooks. The selected mode is restored by `focusvpn-gateway-mode.service` after reboot. Do not switch to WireGuard until the external peer is provisioned and reachable.

## VLESS Automation

Settings provides scheduled VLESS checks with a 1-60 minute interval between completed cycles, a separate automatic-route switch, and Mattermost notifications. New installations default to disabled monitoring/auto-switch and a five-minute interval; enabling checks starts the first cycle immediately. Profiles are tested sequentially in the existing background queue, not in the HTTP request.

The displayed ping is HTTPS time to first byte through the VLESS tunnel, including tunnel setup, not ICMP to the provider IP. Results and timestamps are retained in `/etc/sing-box-admin/outbound-checks/`; failed profiles are red. Only successful VLESS profiles with measured latency participate in auto-selection. The same profile must win three consecutive complete cycles before a switch. Ties prefer the current route. Failures, manual changes, settings/profile edits and service restarts reset the streak; automatic changes are suppressed while WireGuard mode is active.

Successful changes use the shared gateway/HAPP apply-and-rollback transaction. The private journal `/etc/sing-box-admin/gateway-switches.jsonl` retains the latest 500 events; `/gateway-journal` shows the latest 100 under the panel authentication boundary. Settings and state are stored at `/etc/sing-box-admin/vless-monitor-settings.json` and `/etc/sing-box-admin/vless-monitor-state.json` with mode `0600`.

Enter the Mattermost incoming-webhook URL in Settings; the URL is never displayed back or included in journal/API output. Delivery is asynchronous with a bounded timeout, and failures are journaled without undoing the route change. The test-webhook button verifies delivery using the same queue. Run `py -3 scripts/test_vless_monitor.py` and `py -3 scripts/test_route_sync.py` before deploying changes to this feature.

## Rollback

Use the timestamped backup created on the server, restore the specific file, run its syntax check, then restart only the owning service. Never use a blanket reset or overwrite unrelated runtime state.
