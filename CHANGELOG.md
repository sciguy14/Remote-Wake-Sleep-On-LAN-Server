# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Existing installs are unaffected unless they opt in. A `config.php` without the new settings behaves exactly as before. Re-running `python3 setup.py` on an existing install adds one question (the remote access mode, defaulting to port forwarding) and an optional hostname allowlist question (defaulting to off).

### Added
- Remote access modes in the setup wizard: None (LAN only, the default for new installs), Port forwarding (the previous behavior, the default for existing installs) and Cloudflare Tunnel. Re-running the wizard switches modes.
- Cloudflare Tunnel support. The wizard installs `cloudflared` from Cloudflare's apt repository and runs it as the `rwsols-cloudflared` systemd service. The tunnel token is kept in a root-only (0600) environment file, `/etc/cloudflared/rwsols-tunnel.env`, instead of being passed on the command line. `cloudflared` forwards to a loopback-only Apache virtual host (`rwsols-tunnel`, default 127.0.0.1:8080); the LAN listener is not changed.
- Automatic tunnel provisioning through the Cloudflare API (tunnel, ingress rules, proxied CNAME record), and a manual path that takes a tunnel token created in the Cloudflare dashboard. Provisioning is idempotent. Other ingress rules on an existing tunnel are kept, and a conflicting DNS record is shown and left alone unless `--force` is given.
- Optional Cloudflare Access for the tunnel hostname: automatic creation of a self-hosted Access application with an Allow policy (emails and/or an email domain), an optional service token for non-browser clients, or manual entry of the team domain and AUD tag.
- Hostname allowlist (`$ALLOWED_HOSTS`, `$ALLOW_LOCALHOST`, `$LOG_HOST_REJECTIONS`) enforced by a generated Apache configuration (`/etc/apache2/conf-available/rwsols-allowed-hosts.conf`) and again in PHP. Rejected requests get an empty HTTP 421.
- New `config.php` settings: `$ALLOWED_HOSTS`, `$ALLOW_LOCALHOST`, `$LOG_HOST_REJECTIONS`, `$TUNNEL_HOSTNAME`, `$TUNNEL_ORIGIN_BIND`, `$TUNNEL_ORIGIN_PORT`, `$ACCESS_TEAM_DOMAIN`, `$ACCESS_AUD`. All optional.
- Setup script options: `--remote-access`, `--remove-tunnel`, `--dry-run` and `--force`. The Cloudflare API token is read from `CLOUDFLARE_API_TOKEN` or a hidden prompt and kept in memory only.
- `--remove-tunnel` (also offered when switching away from Tunnel mode) removes what the wizard created, as recorded in `.rwsols_remote_access.json`, and lists what it left alone.
- PHP and Python test suites (`tests/php`, `tests/python`).
- Wiki pages: Remote Access with Cloudflare Tunnel, Adding Cloudflare Access (optional), Configuration Reference and Security.

### Changed
- The setup script now asks how RWSOLS will be reached from outside the LAN before the DDNS steps. DDNS, public IP and port 80/443 checks only run in Port forwarding mode. In the other modes the script asks for the LAN name of the Pi and the LAN encryption choice instead.
- Setup logic for remote access lives in a new `rwsols_setup` Python package used by `setup.py`.
- When the request arrives through the tunnel listener, `index.php` does not redirect to HTTPS on its own (Cloudflare terminates TLS), and builds its default redirect with `https://`.

### Security
- Host allowlist protects against DNS rebinding. Matching is exact and case-insensitive; the port and one trailing dot are ignored; there are no wildcards; a missing Host header is rejected. Apache still completes the TLS handshake for an unlisted or missing SNI name; the HTTP request that follows is rejected.
- With Cloudflare Access enabled, requests for the tunnel hostname (or arriving on the tunnel listener) must carry a valid `Cf-Access-Jwt-Assertion` token, verified in PHP (RS256, signing keys from the team's `/cdn-cgi/access/certs`, audience, issuer and expiry checked). Verification fails closed, including when the signing keys cannot be fetched. Setting only one of `$ACCESS_TEAM_DOMAIN` and `$ACCESS_AUD` denies all tunnel requests.
- The identity from a verified Access token is written to syslog (tag `rwsols`) for POST actions.
- Tunnel mode needs no inbound router ports.
