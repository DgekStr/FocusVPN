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
- The admin panel is private-network-only and requires Basic Auth.
- HAPP QR and deeplink endpoints must remain behind the same authorization boundary.
- `9445` is a VLESS inbound, not an admin endpoint.
- Before publishing, run a secret scan and inspect `git diff --cached`.
- Rotate credentials that were ever pasted into chat, logs or test output.
