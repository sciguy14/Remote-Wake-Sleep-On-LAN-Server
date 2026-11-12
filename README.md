REMOTE WAKE/SLEEP-ON-LAN SERVER *(RWSOLS)*
==========================================
The Remote Wake/Sleep-on-LAN Server (RWSOLS) is a simple webapp that runs on your Raspberry Pi to turn it into a remotely accessible Wake/Sleep-On-LAN Server. This is very useful when you have a high-powered machine that you don't want to keep on all the time, but that you want to keep remotely accessible for Remote Desktop, SSH, FTP, etc. Wake-On-LAN packets cannot be forwarded through a router, so to wake up a remote machine behind a router, you need to have something on its local network to wake it up. That's where RWSOLS comes in. RWSOLS can control an unlimited number of remote machines on its local network, and is capable of waking them up (any OS) or putting them to sleep (only Windows remote machines). It can be configured to use signed or unsigned SSL encryption or it can be run over traditional HTTP.
  
A very detailed set of [installation instructions](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki) can be found in the GitHub Wiki. V3 of this software adds an auto-installer script that makes installation very easy, and handles automatic configuration of signed SSL certificate.
  
You'll also find a description of [how it works](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/How-it-Works), [an FAQ](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs), and a list of [relevant terminology](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Terminology) on the Wiki.
  
For more info, see [my blog post about RWSOLS](https://www.jeremyblum.com/2013/07/14/rpi-wol-server/) on my website.
  
If you're having problems with getting RWSOLS working, check the [FAQ](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs) first, or [the comments](https://www.jeremyblum.com/2013/07/14/rpi-wol-server/#comments) on my blog. If you still can't get it to work, please [create a GitHub issue](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/issues) with specific details.


Remote access options
---------------------
The setup script asks how you want to reach RWSOLS from outside your home network. Re-running `python3 setup.py` lets you switch.

- **None (LAN only).** The default for new installs. RWSOLS is only reachable from inside your network.
- **Port forwarding.** The original method: dynamic DNS, router port forwards for 80/443, and an optional Let's Encrypt certificate. This is the default for installs that already exist. See the [installation instructions](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Installation-(v3)).
- **Cloudflare Tunnel.** No inbound ports. A small service (`cloudflared`) on the Pi makes an outbound connection to Cloudflare, which serves your domain. Needs a domain on Cloudflare. See [Remote Access with Cloudflare Tunnel](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Remote-Access-with-Cloudflare-Tunnel).
- **Cloudflare Access (optional, Tunnel mode only).** A login in front of RWSOLS at Cloudflare's edge, which RWSOLS also verifies. See [Adding Cloudflare Access](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Adding-Cloudflare-Access-(optional)).

Separately from the mode, you can turn on a hostname allowlist, so that Apache and PHP answer only to the host names you list (protection against DNS rebinding). See [Security](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Security).

The setup script also offers to turn on automatic security updates for the operating system (Debian's `unattended-upgrades`, extended to the Raspberry Pi package archive), with an optional automatic restart at 04:00 when an update needs one. It asks once, and you can say no.

The setup script installs `python3-rich` (with apt, if it is available) for nicer prompts, and falls back to plain text prompts without it. With `rich` on a Linux terminal, lists are driven with the arrow keys (Space to tick, Enter to accept); otherwise you type the numbers.

Setup script options:

- `python3 setup.py` runs the full setup.
- `python3 setup.py --remote-access` runs only the mode, allowlist, Tunnel and Access questions. Run it after editing the remote access settings in `config.php` by hand.
- `python3 setup.py --remove-tunnel` removes the Tunnel setup that the script created.
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

After editing these by hand, run `python3 setup.py --remote-access` so the copy in `/var/www/html` and the generated Apache configuration are updated.


Upgrading
---------
Nothing changes unless you opt in. A `config.php` without the new settings behaves exactly as before. If you re-run `python3 setup.py` on an existing install, the only visible differences are one new question about the remote access mode (the default is port forwarding, which is the previous behavior), an optional question about the hostname allowlist (the default is off), and a one-time question about automatic security updates (the default is yes; answer no to leave the system as it is).


Moving from port forwarding to Cloudflare Tunnel
------------------------------------------------
1. Put your domain on Cloudflare (nameservers pointed at Cloudflare) if it is not already there.
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
