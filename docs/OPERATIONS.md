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

1. Copy `server/panel/*.py` to `/opt/sing-box-admin/`.
2. Copy `server/panel/static/` to `/opt/sing-box-admin/static/`.
3. Keep root-only files `auth.json`, `wg-easy-api.json` and runtime state on the server.
4. Run Python compilation.
5. `systemctl restart sing-box-admin`.
6. Check panel HTTP status and service status.

## Firewall deployment

The libexec scripts are paired with units in `server/systemd/`. `focusvpn-gateway-mode@.service` switches between VLESS/TProxy and the external `wg-client` interface. Routes are source-policy scoped to `FOCUSVPN_WG_NETWORK`; `FOCUSVPN_LAN_NETWORK` uses the main route table. Apply nft rules only after validation; keep the current ruleset backup for rollback.

The external peer configuration is entered in the authenticated Settings page and stored root-only at `/etc/wireguard/wg-client.conf`. Use a single peer with IPv4 `AllowedIPs = 0.0.0.0/0`; do not add `PostUp`, `PreUp`, or other shell hooks. The selected mode is restored by `focusvpn-gateway-mode.service` after reboot. Do not switch to WireGuard until the external peer is provisioned and reachable.

## Rollback

Use the timestamped backup created on the server, restore the specific file, run its syntax check, then restart only the owning service. Never use a blanket reset or overwrite unrelated runtime state.
