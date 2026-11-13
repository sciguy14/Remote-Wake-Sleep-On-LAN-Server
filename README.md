REMOTE WAKE/SLEEP-ON-LAN SERVER *(RWSOLS)*
==========================================
The Remote Wake/Sleep-on-LAN Server (RWSOLS) is a simple webapp that runs on your Raspberry Pi to turn it into a remotely accessible Wake/Sleep-On-LAN Server. This is very useful when you have a high-powered machine that you don't want to keep on all the time, but that you want to keep remotely accessible for Remote Desktop, SSH, FTP, etc. Wake-On-LAN packets cannot be forwarded through a router, so to wake up a remote machine behind a router, you need to have something on its local network to wake it up. That's where RWSOLS comes in. RWSOLS can control an unlimited number of remote machines on its local network, and is capable of waking them up (any OS) or putting them to sleep (only Windows remote machines). It can be configured to use signed or unsigned SSL encryption or it can be run over traditional HTTP.
  
**More than wake and sleep.** RWSOLS is first of all a wake/sleep server, and that is all you have to use it for. Its setup script can also take care of a few things for your home network as a whole. Each one is optional:

* **Dynamic DNS.** It sets up ddclient so your domain keeps following your home IP address, for RWSOLS or for anything else you run at home (a VPN, a game server).
* **[Cloudflare Tunnel](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Remote-Access-with-Cloudflare-Tunnel)**, with an optional Cloudflare Access login, so RWSOLS is reachable from outside without opening a port on your router.
* **[A trusted HTTPS certificate](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Remote-Access-with-Cloudflare-Tunnel#the-trusted-lan-certificate-dns-challenge)** for the Pi's name on your home network, from Let's Encrypt, renewed automatically. This works for a name inside a domain whose DNS is on Cloudflare, with no port open on your router.
  
A very detailed set of [installation instructions](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki) can be found in the GitHub Wiki. V3 of this software adds an auto-installer script that makes installation very easy, and handles automatic configuration of signed SSL certificate.
  
You'll also find a description of [how it works](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/How-it-Works), [an FAQ](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs), and a list of [relevant terminology](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Terminology) on the Wiki.
  
For more info, see [my blog post about RWSOLS](https://www.jeremyblum.com/2013/07/14/rpi-wol-server/) on my website.
  
If you're having problems with getting RWSOLS working, check the [FAQ](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs) first, or [the comments](https://www.jeremyblum.com/2013/07/14/rpi-wol-server/#comments) on my blog. If you still can't get it to work, please [create a GitHub issue](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/issues) with specific details.


How the setup script is organised
---------------------------------
`python3 setup.py` shows a checklist of seven sections, with what is done and what is left, worked out from what is on the Pi:

1. **Base install**: packages, permissions, web files.
2. **Remote access**: LAN only, port forwarding, or Cloudflare Tunnel (with optional Cloudflare Access).
3. **Dynamic DNS (optional)**: required for port forwarding; optional otherwise, and independent of a tunnel.
4. **HTTPS on your LAN**: the certificate the Pi's own web server uses.
5. **Hostname allowlist**.
6. **Security updates**.
7. **Passphrase and computers**: you set the passphrase, then enter each computer's name, MAC address and IP address as answers to questions. The script writes `config.php` for you; opening it in a text editor is optional.

A first run goes through them in order. You can stop at any time; the next run continues with what is left. Later runs let you run any one section again, so changing one thing does not mean answering everything again. Installs that predate the checklist are recorded as complete.

Remote access options
---------------------
The Remote access section asks how you want to reach RWSOLS from outside your home network. Run that section again to switch.

- **None (LAN only).** The default for new installs. RWSOLS is only reachable from inside your network.
- **Port forwarding.** The original method: dynamic DNS, router port forwards for 80/443, and an optional Let's Encrypt certificate. This is the default for installs that already exist. See the [installation instructions](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Installation-(v3)).
- **Cloudflare Tunnel.** No inbound ports. A small service (`cloudflared`) on the Pi makes an outbound connection to Cloudflare, which serves your domain. Needs a domain whose DNS is managed by Cloudflare (nameservers pointing at Cloudflare; the free plan is enough). How the domain's IP is kept up to date (ddclient on the Pi, your router, another service, or not at all) does not matter. See [Remote Access with Cloudflare Tunnel](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Remote-Access-with-Cloudflare-Tunnel).
- **Cloudflare Access (optional, Tunnel mode only).** A login in front of RWSOLS at Cloudflare's edge (a one-time code emailed to the addresses you allow), which RWSOLS also verifies. You choose how long a login lasts: 1 day, 1 week or 1 month (the default). See [Adding Cloudflare Access](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Adding-Cloudflare-Access-(optional)).

The questions come one decision at a time. First: only at home, or also from outside your home? Home only ends the section there. Second, for outside access: what address you will use, a domain of your own or a free dynamic DNS name from a provider. Third, only for a domain of your own: the script looks up who hosts its DNS and shows the result. If that is Cloudflare, it explains Cloudflare Tunnel and port forwarding side by side and asks which you want. In every other case (a free dynamic DNS name, or a domain whose DNS is elsewhere) only port forwarding is possible, so the script explains it instead of asking, and says what it takes to use Cloudflare Tunnel later. A failed lookup never hides the tunnel.

For the automatic Tunnel setup the script needs a Cloudflare API token. It prints a link that opens Cloudflare's Create Token page with a suggested name and the permissions already filled in, lists only the permissions your answers need, and checks the token you paste before changing anything. If ddclient on the Pi already uses a Cloudflare API token for dynamic DNS, the script offers to use that one, checks that it has the permissions needed (Tunnel, and Access if you enable it), and tells you what is missing if it does not. You can then add the permission in the Cloudflare dashboard and have the script check again without starting over, or paste a different token. The dynamic DNS step mentions this when you create that token.

**Dynamic DNS and a tunnel together.** A tunnel does not use your home IP, so RWSOLS needs no dynamic DNS in Tunnel mode. If you want a domain name to keep following your home IP for something else (a VPN, another service), the Dynamic DNS section sets that up or keeps it running. The tunnel uses its own name (for example `rwsols.example.com`) and dynamic DNS updates a different record (for example `example.com`).

**Certificates.** In Tunnel mode your tunnel address has a certificate that every browser trusts, provided and renewed by Cloudflare, inside and outside your home. A Let's Encrypt certificate that renews over port 80 is not needed any more, and stops renewing once the port forward is gone. The HTTPS section is then only about opening the Pi directly on your LAN: self-signed (browsers warn), plain HTTP, or a trusted Let's Encrypt certificate for a LAN name inside a domain hosted at Cloudflare, obtained with a DNS challenge (no open port; a DNS-only API token is stored on the Pi, readable only by root, for renewals). A `.local` name or an IP address can never get a trusted certificate.

Separately from the mode, you can turn on a hostname allowlist, so that Apache and PHP answer only to the host names you list (protection against DNS rebinding). See [Security](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Security).

The setup script also offers to turn on automatic security updates for the operating system (Debian's `unattended-upgrades`, extended to the Raspberry Pi package archive), with an optional automatic restart at 04:00 when an update needs one. It asks once, and you can say no.

The setup script installs `python3-rich` (with apt, if it is available) for nicer prompts, and falls back to plain text prompts without it. With `rich` on a Linux terminal, lists are driven with the arrow keys (Space to tick, Enter to accept); otherwise you type the numbers.

Setup script options:

- `python3 setup.py` shows the checklist and runs what is left, or one section again.
- `python3 setup.py --remote-access` runs only the mode, allowlist, Tunnel and Access questions. Run it after editing the remote access settings in `config.php` by hand.
- `python3 setup.py --remove-tunnel` removes the Tunnel setup that the script created.
- `python3 setup.py --reset` starts over. The same choice is in the menu as "Start over". It asks first (the default is No). It puts `config.php` back to the defaults (passphrase, computers, allowed hosts and remote access settings are cleared, so nobody can log in until setup is finished again), clears the record of finished sections, and removes the Cloudflare Tunnel service and the Apache settings the script added. Your old `config.php` is saved in the setup folder as a hidden file whose name starts with `.config.php.before-reset-`. Installed software, certificates, automatic updates and dynamic DNS are kept (it offers to remove the dynamic DNS configuration as a separate question). Nothing in your Cloudflare account is deleted; use `--remove-tunnel` first if you want that.
- `python3 setup.py --auto-updates` runs only the automatic security updates question (see below).
- `--dry-run` (only with `--remote-access` or `--remove-tunnel`) prints what would change and changes nothing. `--force` (use it with `--remote-access`) replaces a conflicting DNS record for the tunnel hostname.


Configuration
-------------
Every setting is described in the [Configuration Reference](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Configuration-Reference). The settings added for remote access are all optional:

| Setting | Default | Meaning |
|---|---|---|
| `$ALLOWED_HOSTS` | `array()` | Exact host names RWSOLS answers to. Empty means no Host checking. |
| `$ALLOW_LOCALHOST` | `false` | Also accept `localhost`, `127.0.0.1` and `[::1]` when the allowlist is in use. |
| `$LOG_HOST_REJECTIONS` | `false` | Write a rate-limited syslog line for each request rejected by the allowlist. |
| `$TUNNEL_HOSTNAME` | `NULL` | Public hostname served through Cloudflare Tunnel. Must also be in `$ALLOWED_HOSTS` when the allowlist is in use. |
| `$TUNNEL_ORIGIN_BIND` | `"127.0.0.1"` | Loopback address of the Apache listener that `cloudflared` forwards to (used by the setup script). |
| `$TUNNEL_ORIGIN_PORT` | `8080` | Port of that listener (used by the setup script). |
| `$ACCESS_TEAM_DOMAIN` | `NULL` | Cloudflare Access team domain, e.g. `example.cloudflareaccess.com`. |
| `$ACCESS_AUD` | `NULL` | Application Audience (AUD) tag of the Access application. |
| `$LOGIN_MAX_ATTEMPTS` | `5` | Wrong passphrases allowed from one client address before it is locked out. `0` turns the limit off. |
| `$LOGIN_LOCKOUT_SECONDS` | `900` | Length of the lockout, and the period over which wrong passphrases are counted. |

After editing these by hand, run `python3 setup.py --remote-access` so the copy in `/var/www/html` and the generated Apache configuration are updated.


Upgrading
---------
Remote access works as before unless you opt in: a `config.php` without the new settings keeps its mode, and no allowlist, tunnel or Access is enabled. Two security changes do apply to every install after an upgrade:

- **Login throttling.** Five wrong passphrases from one address lock that address out for 15 minutes, even with the right passphrase. Add `$LOGIN_MAX_ATTEMPTS = 0;` to `config.php` for the old behavior.
- **Stricter file permissions.** The setup script makes `/var/www/html/config.php` readable only by root and the web server (`root:www-data`, mode 640), and the `/var/www/html_backup_*` folders readable only by root. Use `sudo` to read them.

Otherwise, re-running `python3 setup.py` on an existing install shows the checklist with every section recorded as done (remote access as port forwarding, the previous behavior) and asks nothing unless you choose a section. The one exception is the automatic security updates section, which is listed as left to do until you answer it once (the default is yes; answer no to leave the system as it is). The dot files of earlier versions are still honored for the one-time steps.


Moving from port forwarding to Cloudflare Tunnel
------------------------------------------------
1. Put your domain's DNS on Cloudflare if it is not already there: add the site in the Cloudflare dashboard and set the nameservers it shows you at your registrar. The setup script checks this and does not offer the Tunnel option until it is done.
2. Pull the latest code and run `python3 setup.py --remote-access`. Choose "Cloudflare Tunnel" and follow the prompts. Pick a hostname that is not already in use, or be ready to let the script show you the conflicting DNS record (`--force` replaces it).
3. If the new hostname is also in your `ddclient` host list (`/etc/ddclient.conf`), remove it there. The tunnel owns that DNS record, and ddclient would keep overwriting it with your IP. The script warns you if it finds an overlap. DDNS itself is not touched.
4. Check that the new hostname works, then remove the port forwards for 80 and 443 from your router.
5. If you used certbot (`--apache`) for a Let's Encrypt certificate, it renews over HTTP-01 on port 80, which stops working once port 80 is closed. Switch to a DNS-01 challenge (for example a certbot DNS plugin), or use a self-signed certificate for the LAN. The tunnel itself does not need your certificate.


Running the tests
-----------------
The PHP and Python tests need no Apache, root or network access. From the repository root:

    php tests/php/run.php
    python -m unittest discover -s tests/python -v

See `tests/php/README.md` and `tests/python/README.md` for details.


x86 Docker Image (Alternate Installation Option)
------------------------------------------------
GitHub user [ex0nuss](https://github.com/ex0nuss) has created an x86 Docker Image for RWSOLS, that you may wish to try out. I have not independently validated its functionality, but it does pull directly from this Repo. You may find the setup of a Docker image to be faster and easier than following the instructions in the Wiki to do a native installation of this application on a Desktop (this is for x86, not ARM). You can find the GitHub Repo for the Docker Image [here](https://github.com/ex0nuss/Remote-Wake-Sleep-On-LAN-Docker), and the DockerHub link [here](https://hub.docker.com/r/ex0nuss/remote-wake-sleep-on-lan-docker).


License
-------
Copyright 2024 [Jeremy Blum](https://www.jeremyblum.com), [Blum Idea Labs, LLC.](https://www.blumidealabs.com)  
This project is licensed under the GPLv3 license (see LICENSE.md for details).  
Please share improvements or remixes with the community, and attribute me (Jeremy Blum, <https://www.jeremyblum.com>) when reusing portions of my code.  
  
Other contributors to this work include:
- Felix Ryan (https://www.felixrr.pro)
