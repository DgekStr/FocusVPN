# Security notes

- Production credentials are excluded from this repository.
- Do not commit VLESS links, UUIDs, Reality keys, Basic Auth hashes, API passwords, QR payloads or SQLite databases.
- Keep `/etc/sing-box-admin/auth.json`, `wg-easy-api.json`, `happ-server.json` and `vless-uri` root-readable only.
- The admin panel is private-network-only and requires Basic Auth.
- HAPP QR and deeplink endpoints must remain behind the same authorization boundary.
- `9445` is a VLESS inbound, not an admin endpoint.
- Before publishing, run a secret scan and inspect `git diff --cached`.
- Rotate credentials that were ever pasted into chat, logs or test output.
