# Security notes

- Production credentials are excluded from this repository.
- Do not commit VLESS links, UUIDs, Reality keys, Basic Auth hashes, API passwords, QR payloads or SQLite databases.
- Keep `/etc/sing-box-admin/auth.json`, `wg-easy-api.json`, `happ-server.json` and `vless-uri` root-readable only.
- Keep the external WireGuard client configuration at `/etc/wireguard/wg-client.conf` mode `0600`; it contains a private key and must not be backed up into the repository.
- The original external WireGuard profile, including optional DNS text, is retained at `/etc/focusvpn/wireguard-client-input.conf` mode `0600` so the authenticated Settings form can repopulate it. Treat it as private-key material.
- Mattermost incoming-webhook URLs are secrets. Keep `/etc/sing-box-admin/vless-monitor-settings.json` mode `0600`; never include the URL in API responses, journal events, screenshots, repository files or error output.
- Probe results, automation state and `/etc/sing-box-admin/gateway-switches.jsonl` are private runtime data, not publication artifacts. Checks use temporary loopback SOCKS listeners; candidate files containing credentials are mode `0600` and removed after the test process stops.
- Import and automation settings use the panel session/CSRF boundary. A background check must not change the active route; an automatic switch must recheck mode, configuration and revision before applying.
- TLS verification remains enabled unless an imported profile explicitly opts out. A certificate/auth failure must be shown as a failed connection test, not silently bypassed to obtain a green result.
- The admin panel defaults to private networks and requires Basic Auth/session authentication. Explicit `FOCUSVPN_ADMIN_NETWORK=0.0.0.0/0` permits all IPv4 sources without removing authentication or subscription-token checks. On the current server this wider access is enabled by request; port 9443 still uses unencrypted HTTP. Network reachability must not be mistaken for confidentiality: deploy HTTPS before transmitting credentials across untrusted networks.
- HAPP QR and deeplink endpoints must remain behind the same authorization boundary.
- The narrowly scoped `/happ-subscription/<token>` route uses a per-account bearer token and the configured source-network filter, not an admin session. It must never expose another account, registry or admin API. Subscription URLs and `happ-subscription-key.json` (mode `0600`) are credentials: do not log, publish or screenshot them. Disabled, expired and deleted accounts must not receive configurations. Plain HTTP exposes these credentials in transit; use HTTPS for untrusted or internet access.
- Persistent HAPP statistics contain sensitive names, source addresses and visited destinations. Keep `/mnt/stat` mode `0700` and `happ-stat.sqlite3`/SQLite sidecars mode `0600`; history and XLS endpoints require panel authentication. Do not publish the database, screenshots of real user history, backups or downloaded XLS exports. Retention in the database does not erase external copies or filesystem snapshots.
- VIP snapshots and personal UUIDs/links are credentials: keep `happ-vip.json`, `happ-users.json` and `happ-user-events.json` under `/etc/sing-box-admin/` mode `0600`, never publish them. The VIP snapshot includes protected inbound TLS material. Do not include personal UUIDs in audit events or infer user identity from a shared client IP.
- `9445` is a VLESS inbound, not an admin endpoint.
- Before publishing, run a secret scan and inspect `git diff --cached`.
- Rotate credentials that were ever pasted into chat, logs or test output.
