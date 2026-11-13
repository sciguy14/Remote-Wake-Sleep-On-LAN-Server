#! /usr/bin/python3

# Remote Wake/Sleep-On-LAN Server [SETUP SCRIPT]
# https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
# (c) 2024 Blum Idea Labs, LLC.
# Author: Jeremy E. Blum (https://www.jeremyblum.com)
# License: GPL v3 (http://www.gnu.org/licenses/gpl.html)

# Import Libraries
import subprocess
import pathlib
import os
import signal
import sys
import urllib.request
import urllib.error
import re
import socket
import time
import ssl
import fileinput

import datetime
import argparse
from rwsols_setup import ui as ui_mod

# Exit Handler
def signal_handler(sig, frame):
    if ui is not None:
        ui.error('\n\nABORTING SCRIPT.\n')
    else:
        sys.stderr.write('\n\nABORTING SCRIPT.\n\n')
    sys.exit(1)
signal.signal(signal.SIGINT, signal_handler)

# Global Variables
script_dir = pathlib.Path(__file__).parent.absolute()
public_ipv4 = "127.0.0.1"
urls = []
encryption_mode="none" # Options: "none","self","certbot","dns","skip"
dns_cert_names = [] # LAN name(s) on the DNS-challenge certificate, when that option was chosen in this run
ddns_temp_config=pathlib.Path('/tmp/ddclient.conf')
ddns_real_config=pathlib.Path('/etc/ddclient.conf')
rwsols_config_sample = script_dir.joinpath('www/html/config_sample.php')
rwsols_config_user = script_dir.joinpath('www/html/config.php')
remote_mode = "port_forward" # Options: "none","port_forward","tunnel" (how RWSOLS is reached from outside the LAN)

# The wizard UI (rich when available on a TTY, plain text otherwise). Created in main().
ui = None

# Shared context for the rwsols_setup package (command runner, HTTP, state file). Created in main().
ctx = None

# Command line options
def parse_args():
    parser = argparse.ArgumentParser(description='Remote-Wake-Sleep-On-LAN-Server (RWSOLS) setup script')
    parser.add_argument('--remote-access', action='store_true', help='Only run the remote access part (mode, hostname allowlist, Cloudflare Tunnel/Access).')
    parser.add_argument('--remove-tunnel', action='store_true', help='Remove the Cloudflare Tunnel setup created by this script.')
    parser.add_argument('--reset', action='store_true', help='Put the RWSOLS settings back to their defaults (after asking) and start setup over.')
    parser.add_argument('--auto-updates', action='store_true', help='Only run the automatic security updates step (unattended-upgrades).')
    parser.add_argument('--dry-run', action='store_true', help='With --remote-access/--remove-tunnel: print what would be done and change nothing (read-only API calls still happen).')
    parser.add_argument('--force', action='store_true', help='With --remote-access: replace a conflicting DNS record for the tunnel hostname.')
    args = parser.parse_args()
    if (args.dry_run or args.force) and not (args.remote_access or args.remove_tunnel):
        parser.error('--dry-run and --force require --remote-access or --remove-tunnel')
    return args

def make_context(args):
    from rwsols_setup.runtime import Context, SystemRunner, UrllibTransport
    return Context(SystemRunner(), UrllibTransport(), ui, str(script_dir), dry_run=args.dry_run, force=args.force)

# Main Logic
def main():
    global urls, remote_mode, ui, ctx
    args = parse_args()
    ui_mod.bootstrap_rich(skip=args.dry_run)
    ui = ui_mod.select_ui()
    from rwsols_setup import sections, wizard
    from rwsols_setup.errors import SetupError
    ctx = make_context(args)

    if args.auto_updates:
        _09b_auto_updates()
        return

    if args.remove_tunnel or args.remote_access:
        try:
            if args.remove_tunnel:
                wizard.run_remove_tunnel(ctx, copy_webroot=_12_copy_webroot)
            else:
                wizard.run_remote_access(ctx, urls, get_local_ip() or None, copy_webroot=_12_copy_webroot)
                if not args.dry_run:
                    sections.mark_done(ctx, 'remote')
                    sections.mark_done(ctx, 'allowlist')
        except SetupError as e:
            ui.error("\n" + str(e))
            sys.exit(1)
        return

    # Intro
    ui.say("")
    ui.heading("Remote-Wake-Sleep-On-LAN-Server (RWSOLS) Setup Script")

    # Warn the user that internet scripts are dangerous
    ui.explain("Before you continue", [
        "This script will install the required packages and configure permissions as necessary for RWSOLS to work properly. It assumes you are on the bookworm release.",
        "As with all scripts that you find on the internet, I strongly suggest you read its contents before blindly executing it.",
        "This script assumes your user is a passwordless sudo user, which is the default for the 'pi' user on Raspbian. It executes commands with sudo.",
        "If you want to abort this script to inspect its contents, press Ctrl+C now."], style="warn")
    ui.pause("If you have read and understood what this script is doing, Press Enter to continue with the automatic setup.")
    ui.say("")

    # The setup is a checklist of sections. It shows what is done and what is left, runs the rest in order,
    # and lets any one section be run again later.
    runners = {
        'base': section_base,
        'remote': section_remote,
        'ddns': section_ddns,
        'https': section_https,
        'allowlist': section_allowlist,
        'updates': section_updates,
        'config': section_config,
    }
    # --reset: wipe the settings first (it asks before changing anything), then carry on with the now-empty checklist
    if args.reset:
        from rwsols_setup import reset
        from rwsols_setup.state import load_state
        try:
            if not reset.run(ctx, load_state(ctx.state_path)):
                return
        except (SetupError, OSError) as e:
            ui.error("\n" + str(e))
            sys.exit(1)

    # Keep a configuration file written by an earlier version of this script current (no questions asked)
    try:
        from rwsols_setup import hardening
        if hardening.refresh_extra_conf(ctx.runner):
            ui.hint("Automatic security updates now also cover cloudflared (Cloudflare Tunnel).")
    except SetupError:
        pass

    if not sections.run(ctx, runners, lambda: wizard.ddclient_hosts(ctx.runner)):
        sys.exit(1)


# ---------------------------------------------------------------------------------------------------------
# Sections of the setup checklist (order and status tracking live in rwsols_setup/sections.py).
# Each returns True when it finished.
# ---------------------------------------------------------------------------------------------------------

# The remote access mode chosen in the "Remote access" section ("none" until one is chosen)
def current_mode():
    from rwsols_setup import sections
    return sections.load(ctx).get('mode') or 'none'

# Section 1: packages, permissions, PHP hardening, a default config.php and the web files
def section_base():
    from rwsols_setup import sections
    again = bool(sections.load(ctx)['sections'].get('base')) # Chosen from "Run one section again": redo the one-time steps too
    run_step('_01_install_prereqs', 'Install Prerequisite Software', force=again)
    run_step('_02_ping_permissions', 'Grant Ping Permission', force=again)
    run_step('_09_secure_php', 'Secure the PHP Installation', force=again)
    run_step('_10_prep_config_file', 'Generate Default WOL Config File', False)
    run_step('_03_copy_webroot', 'Copy Webroot', False)
    return True

# Section 2: how RWSOLS is reached from outside the LAN (and the Cloudflare Tunnel / Access setup, if chosen)
def section_remote():
    global remote_mode
    from rwsols_setup import sections, wizard
    from rwsols_setup.errors import SetupError
    before = sections.load(ctx).get('mode')
    done_before = dict(sections.load(ctx)['sections'])
    try:
        remote_mode = wizard.choose_mode(ctx, heading=False)
        wiz = wizard.RemoteAccessWizard(ctx, remote_mode, [], get_local_ip() or None)
        wiz.plan(parts=('mode',))
        wiz.prepare_cloud()
        wiz.write_config()
        run_step('_12_copy_webroot', 'Copy Configured Webroot', False)
        wiz.apply_local()
    except SetupError as e:
        ui.error("\n" + str(e))
        return False
    if before != remote_mode:
        # The certificate choices depend on the mode, and port forwarding cannot work without dynamic DNS.
        redo = ['https'] + (['ddns'] if remote_mode == "port_forward" and not wizard.ddclient_hosts(ctx.runner) else [])
        redo = [k for k in redo if done_before.get(k)] # Sections still on the list anyway need no announcement
        if redo:
            sections.unmark(ctx, *redo)
            ui.hint("Because the remote access mode changed, setup will also go through: " + ", ".join(sections.TITLES[k] for k in redo) + ".")
    return True

# Section 3: dynamic DNS. Required for port forwarding. Optional otherwise, and independent of a Cloudflare Tunnel.
def section_ddns():
    global urls
    from rwsols_setup import sections, wizard
    mode = current_mode()
    required = (mode == "port_forward")
    hosts = wizard.ddclient_hosts(ctx.runner)
    urls = []
    if required:
        ui.explain("Why you need it", [
            "Port forwarding needs a domain name that always points at your home IP address, which most internet providers change from time to time. Dynamic DNS keeps the name up to date.",
            "This Pi can do the updating (with ddclient), or your router or another device can."])
    else:
        ui.explain("Do you need it?", [
            "With " + sections.MODE_NAMES.get(mode, mode) + ", RWSOLS itself does not need dynamic DNS.",
            "Set it up only if you want a domain name to keep following your home IP address for something else: a VPN, a game server, another website on your network.",
            "It lives happily next to a Cloudflare Tunnel. The tunnel uses its own name (for example rwsols.example.com), and dynamic DNS keeps updating a different record (for example example.com)."])
    if hosts:
        ui.info("Dynamic DNS is already set up on this Pi for: " + ", ".join(hosts))
        options = [("keep", "Keep it as it is."), ("pi", "Set it up again on this Pi.")]
        if not required:
            options.append(("off", "Turn it off on this Pi."))
    elif required:
        options = [("router", "My router or another device handles dynamic DNS."), ("pi", "Set up dynamic DNS on this Pi.")]
    else:
        options = [("none", "No dynamic DNS."), ("router", "My router or another device already handles it."), ("pi", "Set up dynamic DNS on this Pi.")]
    c, _ = ui.choose("What would you like to do about dynamic DNS?", [label for _, label in options])
    choice = options[c-1][0]
    ui.say("")

    if choice == "none":
        sections.update(ctx, ddns="none")
        return True
    if choice == "off":
        try:
            subprocess.run(['sudo', 'systemctl', 'disable', '--now', 'ddclient'], check=True)
        except subprocess.CalledProcessError as e:
            ui.warn("Error stopping the ddclient service.")
            ui.warn(str(e))
            return False
        ui.say("ddclient is stopped and will not start at boot. Its configuration file was left in place.")
        sections.update(ctx, ddns="none")
        return True
    if choice == "pi":
        if not _04_setup_ddns(2):
            return False
    elif choice == "router":
        _04_setup_ddns(1)
    sections.update(ctx, ddns=("router" if choice == "router" else "pi"))

    # Check that the name(s) now point at this network's public IP
    if _05_get_ip() and _06_check_urls():
        return True
    if required:
        return False
    ui.warn("The check failed, but dynamic DNS is optional in this mode, so setup continues. Run this section again once it is fixed.")
    return True

# Section 4: how the Pi's own web server is encrypted (certificates)
def section_https():
    global urls, encryption_mode, dns_cert_names
    from rwsols_setup import sections
    mode = current_mode()
    urls = []
    dns_cert_names = []
    encryption_mode = "skip"
    if mode == "port_forward":
        # Public names: find out what is being served on ports 80/443 to offer the right choices
        fill_urls_var()
        if not _07_server_check():
            return False
    elif not _07_lan_encryption_check():
        return False

    if encryption_mode == "certbot":
        run_step('_08_certbot_setup', 'Certbot-Signed SSL Certificate Setup', False)
    elif encryption_mode == "dns":
        run_step('_08_dnscert_setup', 'Trusted LAN Certificate Setup (DNS challenge)', False)
    elif encryption_mode == "self":
        run_step('_08_selfcert_setup', 'Self-Signed SSL Certificate Setup', False)
    elif encryption_mode == "none":
        run_step('_08_unencrypted_setup', 'Unencrypted Setup', False)
    else:
        ui.say("No Apache2 encryption setting changes will be made.\n")
    if encryption_mode != "skip":
        sections.update(ctx, https={'kind': encryption_mode, 'names': list(dns_cert_names)})

    # config.php needs to know whether to send visitors to HTTPS
    if not _set_use_https():
        return False
    run_step('_12_copy_webroot', 'Copy Configured Webroot', False)
    return True

# Section 5: the hostname allowlist
def section_allowlist():
    from rwsols_setup import wizard
    from rwsols_setup.errors import SetupError
    try:
        wiz = wizard.RemoteAccessWizard(ctx, current_mode(), [], get_local_ip() or None)
        wiz.plan(parts=('allowlist',))
        wiz.write_config()
        run_step('_12_copy_webroot', 'Copy Configured Webroot', False)
        wiz.apply_local(service=False)
    except SetupError as e:
        ui.error("\n" + str(e))
        return False
    return True

# Section 6: automatic security updates for the operating system
def section_updates():
    return _09b_auto_updates()

# Section 7: the passphrase and the list of computers. Last, because it ends in a text editor.
def section_config():
    if not _11_modify_config_file():
        return False
    run_step('_12_copy_webroot', 'Copy Configured Webroot', False)
    return True


# Function for encapsulating parts of the setup process that can be skipped on script re-run
# handle: short name for setup step
# description: human-understandable setup name
# dot_file_skippable: If True, this step will be skipped if already completed in previous run
# Returns True if the setup step should be run, False if it was already previously completed
def run_step(handle, description, dot_file_skippable=True, force=False):
    if script_dir.joinpath("." + handle).exists() and dot_file_skippable and not force:
        ui.info("'" + description + "' has already been completed. Skipping.\n")
    else:
        ui.info("Running: " + description)
        if eval(handle + '()'):
            ui.success("Done: " + description + "\n")
            if dot_file_skippable:
                script_dir.joinpath("." + handle).touch()
        else:
            ui.error("Script Exiting due to failure in '" + description + "' setup step.\n")
            sys.exit(1) 

# Setup Step 1: Install Prerequisite Software
def _01_install_prereqs():
    try:
        # Update the APT Cache
        subprocess.run(['sudo', 'apt-get', 'update'], check=True)

        # Use the latest available php version.
        php_pkg = subprocess.run("apt-cache search --names-only ^php[0-9]+\.[0-9]+$ | awk '{print $1}'", shell=True, check=True, capture_output = True, text = True)
        php_curl_pkg = subprocess.run("apt-cache search --names-only ^php[0-9]+\.[0-9]+\-curl$ | awk '{print $1}'", shell=True, check=True, capture_output = True, text = True)
        php_apache_pkg = subprocess.run("apt-cache search --names-only ^libapache2-mod-php[0-9]+\.[0-9]+$ | awk '{print $1}'", shell=True, check=True, capture_output = True, text = True)

        # Install packages
        subprocess.run(['sudo', 'apt-get', '-y', 'install', 'wakeonlan', 'git', 'apache2', php_pkg.stdout.rstrip(), php_curl_pkg.stdout.rstrip(), php_apache_pkg.stdout.rstrip(), 'snapd'], check=True)
        subprocess.run(['sudo', 'snap', 'install', 'core'], check=True)
        subprocess.run(['sudo', 'snap', 'refresh', 'core'], check=True)
        subprocess.run(['sudo', 'apt-get', '-y', 'remove', 'certbot']) # Remove any existing installations from package managers
        subprocess.run(['sudo', 'snap', 'install', '--classic', 'certbot'], check=True)
        subprocess.run(['sudo', 'ln', '-sf', '/snap/bin/certbot', '/usr/bin/certbot'], check=True)
    except subprocess.CalledProcessError:
        ui.warn("Error installing prerequisite software.")
        return False
    return True

# Setup Step 2: The PHP server uses the built-in ping command to check if the remote machine is awake or not. Give all users on the pi permission to ping.
def _02_ping_permissions():
    try:
        subprocess.run(['sudo', 'chmod', 'u+s', '/usr/bin/ping'], check=True)
    except subprocess.CalledProcessError:
        ui.warn("Error setting ping permissions.")
        return False
    return True

# Setup Step 3: Copy unconfigured webroot to default apache directory
def _03_copy_webroot():
    wol_html_dir = script_dir.joinpath('www/html')
    apache_www_dir = pathlib.Path('/var/www')
    apache_html_dir = apache_www_dir.joinpath('html')
    try:
        if directories_match(wol_html_dir, apache_html_dir):
            ui.info("Apache webroot contents already match local webroot contents. No update to be made.")
        else:
            ui.warn("The Apache webroot contents do not match local webroot.")
            backup_dir_str = str(apache_www_dir) + "/html_backup_" + datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            subprocess.run(['sudo', 'mv', str(apache_html_dir), backup_dir_str], check=True)
            ui.warn("Apache webroot backed up to " + backup_dir_str)
            subprocess.run(['sudo', 'cp', '-R', str(wol_html_dir), str(apache_www_dir)], check=True)
            ui.success("Website contents copied to Apache webroot.")
    except Exception as e:
        ui.warn("Error moving website contents to Apache webroot folder.")
        ui.warn(str(e))
        return False
    secure_webroot_permissions()
    return True

# config.php holds the passphrase hash: keep the served copy, and old webroot backups, away from other local users.
# A failure here is reported but does not stop the setup.
def secure_webroot_permissions():
    from rwsols_setup import hardening
    from rwsols_setup.errors import SetupError
    from rwsols_setup.runtime import SystemRunner
    try:
        for line in hardening.secure_webroot(SystemRunner()):
            ui.hint("Permissions: " + line + ".")
    except SetupError as e:
        ui.warn("Could not tighten the permissions of the Apache webroot:")
        ui.warn(str(e))

# Setup Step 4: DDNS Configuration
def _04_setup_ddns(ddns_option=None):
    if ddns_option is None:
        ddns_option, _ = ui.choose('Will you handle Dynamic DNS updates from your router, or would you like to setup Dynamic DNS updating on this Pi?', ['I will handle DDNS on my router or elsewhere.','I want to configure DDNS updates from this Pi.'])
    ui.say("")
    if ddns_option == 1:
        ui.say("Ok. If you haven't already, go configure your router or other device to update your DDNS service of choice, now.")
        ui.pause("Once you've done that, press Enter to proceed, and the URL/IP will be checked.")
        return True
    elif ddns_option == 2:
        # Setup ddclient so that our URL points to our public IP. If we don't do this, certbot won't be able to complete the port 80 challenge.

        # Note, we assume user is on at least bookworm release, as that has version 3.10 of ddclient
        ui.pause("ddclient will be installed via apt package manager. Snap version will be removed if present. Press enter to proceed.")
        ui.say("")
        ui.say("Installing ddclient and required packages...")
        try:
            subprocess.run(['sudo', 'snap', 'remove', '--purge', 'ddclient-snap']) # Remove any existing installations from snap
            subprocess.run(['sudo', 'DEBIAN_FRONTEND=noninteractive', 'apt', 'install', '-yqq', 'libio-socket-ssl-perl', 'ddclient'], check=True)
        except subprocess.CalledProcessError as e:
            ui.warn("Error installing ddclient and/or libio-socket-ssl-perl.")
            ui.warn(str(e))
            return False


        ui.say("\nNow we need to setup ddclient on this device.")
        ddclient_config_option, _ = ui.choose("Would you like to use this script's wizard to help you generate your ddclient config file, or would you like to provide your own config file?", ['Please help me generate the config file.','I will manually create the ddclient config file (advanced!).'])
        if ddclient_config_option == 1:
            ui.say("\nGreat! Answer the following questions to generate your config file. You'll have a chance to review it and re-answer if you make a mistake.")
            # Now, get the info necessary to create the ddclient.conf file
            while True:
                with open(ddns_temp_config, 'w') as f:
                    f.write('# Generated by RWSOLS Setup Script: https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server\n')
                    f.write('usev4=webv4,webv4=ipify-ipv4\n') # We are running this behind a router, so we must use the web method to get the IP. The default google domains lookup is gone, so we use ipify instead for ipv4.
                    f.write('ssl=yes\n') # Don't send our password in cleartext!
                    i, v = ui.choose('Choose a DDNS Protocol.', ['dyndns2','noip','cloudflare','namecheap','zoneedit1','easydns','nfsn','yandex','ovh','dinahosting'])
                    f.write('protocol=' + v + '\n')
                    url_prediction = ''
                    # Cloudflare needs some special configuration.
                    if v == 'cloudflare':
                        ui.pause("\nIn order to configure DDNS for a domain whose DNS records are managed by cloudflare, you must first ensure that your selected domain's DNS configuration is setup to have its A-record NOT proxied through cloudflare (must have the grey cloud icon in the web dashboard instead of the orange proxy cloud). Press enter once you have confirmed this.")
                        while True:
                            v = ui.ask('\nEnter your cloudflare DNS zone name to be controlled (ex: myhome.com)')
                            if is_valid_hostname(v):
                                f.write('zone=' + v + '\n')
                                url_prediction = v
                                break
                            else:
                                ui.say("'" + v + "' is not a tld zone name. Please try again.")
                        f.write('login=token\n') # Cloudflare requires the use of an API token, so no username is entered.
                        ui.say("\nFollow the instructions at this link to create an API token for your cloudflare account. It must have the permissions shown in the screenshot.")
                        ui.say("https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs#cloudflare-ddns")
                        from rwsols_setup import wizard as wizard_mod
                        ui.say("\nOr open this link, which fills in the token name (" + wizard_mod.ddns_token_name(url_prediction) + ") and the two permissions for you. On that page, narrow \"Zone Resources\" to " + url_prediction + ", then choose Continue to summary and Create Token:")
                        ui.link(wizard_mod.ddns_token_url(url_prediction))
                        note = wizard_mod.ddns_token_note(current_mode() if ctx is not None else None)
                        if note:
                            ui.explain(note[0], note[1])
                        v = ui.ask_secret('\nEnter the generated cloudflare API token', strip=False)
                        f.write('password=' + v + '\n')
                    else:
                        while True:
                            v = ui.ask('Enter your Server URL (ex: members.dyndns.org). Leave blank if not needed by your service/protocol')
                            if v == "":
                                break # No Server value provided
                            elif is_valid_hostname(v):
                                f.write('server=' + v + '\n')
                                break
                            else:
                                ui.say("'" + v + "' is not a valid server. Please try again.")
                        v = ui.ask('Enter your login username for the service')
                        f.write('login=' + v + '\n')
                        v = ui.ask_secret('Enter your login password for the service', strip=False)
                        f.write('password=' + v + '\n')
                    global urls
                    urls = []
                    if url_prediction != '':
                        url_fill_option, _ = ui.choose('You set ' + url_prediction + ' as your zone. Would you like to use it as your single URL, or do you want to specify different or multiple hostnames?',['Set ' + url_prediction + ' as my single URL to point at my IP.', 'I want to set a different, or multiple URLS (i.e. subdomains) in this zone to point at my IP'])
                        if url_fill_option == 1:
                            urls = [url_prediction]
                    if len(urls) == 0:
                        if url_prediction == '':
                            urls = enter_urls('Enter all your Hostname(s) to update (ex: myhome.com) (multi ex: sub1.myhome.com, myhome.com).')
                        else:
                            urls = enter_urls('Enter all your Hostname(s) to update (ex: ' + url_prediction + ') (multi ex: sub1.' + url_prediction + ', ' + url_prediction + ').')
                    f.write(",".join(urls) + '\n')
                ui.say("\nYour config file contents will be written as follows:")
                with open(ddns_temp_config, 'r') as f:
                    for line in f:
                            ui.say("\t" + line.strip())
                    config_okay, _ = ui.choose('Does this look okay?',['Yes Please write this configuration file', 'No. I want to do the wizard again.'])
                    if config_okay == 1:
                        break
                    else:
                        ui.say("\nOkay. The wizard will run again.\n")
            try:
                subprocess.run(['sudo', 'cp', str(ddns_temp_config), str(ddns_real_config)], check=True)
            except subprocess.CalledProcessError:
                ui.warn("Error copying config file.")
                return False
        elif ddclient_config_option == 2:
            ui.say("Okay. It recommend that you set use=web and ssl=yes. When you're done editing, save and exit nano to proceed.")
            ui.pause("Press enter to open the config file with nano.")
            try:
                subprocess.run(['sudo', 'nano', str(ddns_real_config)], check=True)
            except subprocess.CalledProcessError:
                ui.warn("Error editing config file.")
                return False
            ui.say("The config file has been written.\n")

        ui.say("Launching ddclient daemon...")
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'ddclient'], check=True)
        except subprocess.CalledProcessError:
            ui.warn("Error launching ddclient daemon.")
            return False

        ui.say("Waiting 5 seconds for DNS update to execute before proceeding...")
        time.sleep(5)    
        return True

# Setup Step 5: Get Public IP -  we'll need it for checking existing DDNS configs, or for configuring a new one
def _05_get_ip():
    global public_ipv4
    try:
        public_ipv4 = urllib.request.urlopen('https://v4.ident.me').read().decode('utf8')
    except urllib.error.URLError as e:
        if hasattr(e, 'reason'):
            ui.warn('Failed to reach the ident.me server.')
            ui.warn('Reason: ' + str(e.reason))
        elif hasattr(e, 'code'):
            ui.warn('The ident.me server could not fulfill the request.')
            ui.warn('Error code: ' + str(e.code))
        return False
    ui.info("The public-facing IPv4 address of this Pi's network was detected as " + public_ipv4 + ".")
    return True

# Setup Step 6: Confirm that the DNS update succeeded
def _06_check_urls():
    fill_urls_var()
    max_attempts = 7
    backoff_exponent = 2

    # Now we check to see if each of the URLs resolves to our public-facing IP address.
    failures = False
    for url in urls:
        try:
            resolved_ip = socket.gethostbyname(url)
        except socket.gaierror:
            ui.warn("Failed to resolve " + url + "! Is it a valid URL?")
            failures = True
        else:
            for attempt in range(1, max_attempts+1):
                if resolved_ip != public_ipv4:
                    if attempt < max_attempts:
                        ui.warn("[Attempt " + str(attempt) + "/" + str(max_attempts) + "] " + url + " resolves to " + resolved_ip + " instead of detected public IP of " + public_ipv4 + "! Checking again in " + str(attempt**backoff_exponent) + " seconds.")
                        time.sleep(attempt**backoff_exponent)
                    else:
                        ui.warn("[Attempt " + str(attempt) + "/" + str(max_attempts) + "] " + url + " resolves to " + resolved_ip + " instead of detected public IP of " + public_ipv4 + "! DDNS configured incorrectly, or it hasn't updated yet.")
                        failures = True
                else:
                    ui.success("[Attempt " + str(attempt) + "/" + str(max_attempts) + "] " + url + " correctly resolves to " + public_ipv4 + "!")
                    break
            else:
                ui.warn(url + " resolves to " + resolved_ip + " instead of detected public IP of " + public_ipv4 + "! ")
    if failures:
        ui.warn("One or more of your URLs failed to resolve! Try running this script again after checking your domains and your DDNS configuration.")
        ui.warn("Run the Dynamic DNS section of this script again if you need to reconfigure local DDNS updating.")
        return False
    return True

# Setup Step 7: Check to see if port 80 and/or port 443 is currently serving RWSOLS to correctly display options for cert setup
def _07_server_check():
    global encryption_mode
    no_encryption_option_text = 'I do not wish to enable encryption. (You can still manually setup certbot later using a Port 80 or DNS challenge).'
    disable_encryption_option_text = 'I wish to disable encryption, making RWSOLS accessible over an unencrypted connection only.'
    switch_to_unsigned_option_text = 'I wish to change from a signed certificate to an unsigned certificate.'
    switch_to_signed_option_text = 'I wish to change from an unsigned certificate to a signed certificate (RECOMMENDED OPTION).'
    retain_encryption_settings_option_text = 'I wish to retain the existing encryption settings.'
    certbot_encryption_option_text = 'Proceed with automatic certbot signed encryption setup (STRONGLY RECOMMENDED OPTION)'
    self_signed_encryption_option_text = 'I would like this script to automatically configure encryption using a self-signed certificate.'
    exit_and_fix_option_text = 'Please exit the script - I will forward port 80 and run the script again once I have done that.'
    
    http_server, http_rwsols, https_server, https_rwsols, https_signed = rwsols_serving_status()
    ui.say("")

    # RWSOLS is already running with a signed cert
    if https_rwsols and https_signed:
        ui.say("Your RWSOLS is already accessible over an encrypted connection with a valid signed certificate.")
        c, _ = ui.choose('How would you like to proceed?', [retain_encryption_settings_option_text, switch_to_unsigned_option_text, disable_encryption_option_text])
        if c == 1:
            encryption_mode="skip"
        elif c == 2:
            encryption_mode="self"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is already running with a self-signed cert, and port 80 is accessible
    elif http_rwsols and https_rwsols and not https_signed:
        ui.say("Your RWSOLS is already accessible over an encrypted connection, but it is using a self-signed certificate.")
        ui.say("Port 80 is also correctly resolving to RWSOLS, so automatic certbot configuration should be possible, and is recommended.")
        c, _ = ui.choose('How would you like to proceed?', [switch_to_signed_option_text, retain_encryption_settings_option_text, disable_encryption_option_text])
        if c == 1:
            encryption_mode="certbot"
        elif c == 2:
            encryption_mode="skip"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is already running with a self-signed cert and port 80 is not accessible
    elif not http_rwsols and https_rwsols and not https_signed:
        ui.say("Your RWSOLS is already accessible over an encrypted connection, but it is using a self-signed certificate.")
        ui.say("Port 80 does not appear to be open and pointed to RWSOLS, so automatic certbot configuration is not possible.")
        c, _ = ui.choose('How would you like to proceed?', [exit_and_fix_option_text, retain_encryption_settings_option_text, disable_encryption_option_text])
        if c == 1:
            local_ip = get_local_ip()
            if local_ip:
                ui.info("\nOk! Forward ports 80 and 443 to this Pi (your Pi's local IP that you should forward to is " + local_ip + ").")
            return False
        elif c == 2:
            encryption_mode="skip"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is running on port 80, but doesn't have encryption setup
    elif http_rwsols:
        ui.say("It looks like RWSOLS is active on port 80, but HTTPS is not configured.")
        ui.say("Certbot should be able to automatically issue and configure a signed, auto-renewing encryption certificate.")
        c, _ = ui.choose('How would you like to proceed?', [certbot_encryption_option_text, self_signed_encryption_option_text, no_encryption_option_text])
        if c == 1:
            encryption_mode="certbot"
        elif c == 2:
            encryption_mode="self"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is not currently accessible
    else:
        ui.say("In order to automatically configure SSL Encryption, port 80 must be open on your router and forwarded to port 80 on this Pi.")
        ui.say("If you are unable to forward port 80 because you are using it for another service, or because your ISP blocks it, you can manually configure certbot to perform a DNS challenge instead, or this script can setup unsigned encryption.")
        c, _ = ui.choose('How would you like to proceed?', [exit_and_fix_option_text , self_signed_encryption_option_text, no_encryption_option_text])
        if c == 1:
            local_ip = get_local_ip()
            if local_ip:
                ui.info("\nOk! Forward ports 80 and 443 to this Pi (your Pi's local IP that you should forward to is " + local_ip + ").")
            return False
        elif c == 2:
            encryption_mode="self"
        else:
            encryption_mode="none"
        return True


# Setup Step 7 (None/Tunnel modes): there is no public URL served by this Pi's Apache, so only ask about LAN encryption, using a LAN name
def _07_lan_encryption_check():
    global urls, encryption_mode
    default_name = socket.gethostname().split('.')[0] + '.local'
    lines = []
    tunnel_host = None
    if ctx is not None:
        from rwsols_setup import sections
        st = sections.load(ctx)
        if st.get('mode') == "tunnel":
            tunnel_host = (st.get('tunnel') or {}).get('hostname')
    if tunnel_host:
        lines += [
            "Your tunnel address, https://" + tunnel_host + ", already has a certificate that every browser trusts. Cloudflare provides and renews it, and it works the same inside and outside your home. Using that one address everywhere is the simplest way to never see a browser warning.",
            "A Let's Encrypt certificate that renews through port 80 is not needed with a tunnel. If you have one from a port forwarding setup, it stops renewing once the port forward is gone.",
            "The rest of this section is only about opening RWSOLS directly on your home network (for example by its .local name), which keeps working when your internet connection is down."]
    if pathlib.Path('/etc/apache2/sites-enabled/000-default-le-ssl.conf').exists() and (ctx is None or current_mode() != "port_forward"):
        lines.append("This Pi currently has a Let's Encrypt certificate that renews through port 80. If you keep the existing settings it works until it expires (within 90 days), and it renews only while port 80 is still forwarded to this Pi. Pick one of the other choices to replace it.")
    lines += [
        "The LAN name is how you reach this Pi from your home network (this Pi's mDNS name by default). It is also used as the name on the self-signed certificate, if you choose one.",
        "Self-signed HTTPS encrypts traffic on your LAN, but browsers will show a warning because no authority vouches for the certificate. Plain HTTP has no warning but no encryption.",
        "A .local name or an IP address can never get a certificate that browsers trust. A LAN name inside a domain whose DNS is hosted at Cloudflare can (last option).",
        "Keeping the existing settings changes nothing about how Apache is currently set up."]
    ui.explain("LAN name and encryption", lines)

    def valid_name(v):
        if not is_valid_hostname(v):
            raise ValueError("'" + v + "' is an invalid hostname.")
        return v

    urls = [ui.ask('Name used to reach this Pi on your LAN (also the self-signed certificate name)', default_name, validator=valid_name)]
    options = ['Keep the existing encryption settings.', 'Use a self-signed certificate (HTTPS; browsers will warn).', 'No encryption (plain HTTP on the LAN).']
    modes = ["skip", "self", "none"]
    if ctx is not None:
        options.append("Get a trusted certificate for a LAN name in my Cloudflare domain (Let's Encrypt; a DNS token stays on this Pi).")
        modes.append("dns")
    c, _ = ui.choose('How should RWSOLS be served on your local network?', options)
    encryption_mode = modes[c-1]
    return True
    return True

# Setup Step 8: Certbot config
def _08_certbot_setup():
    # Let's Encrypt!
    ui.say("A valid email address is required so that the Let's Encrypt Certificate Authority can email you if there is problem with the automatic renewal of your certificate.")
    ui.say("This email will NOT be signed up for a mailing list.")
    while True:
        email = ui.ask("Please provide a valid email")
        if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", email):
            ui.warn("That doesn't look like a valid email...")
        else:
            break
    fill_urls_var()

    # In case setup has previously run and has copied over the site config with the SSL port configured, we restore the HTTP-only config so that there is only one virtualhost for certbot
    try:
        bak = copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_http.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error getting Apache2 site config into default state before running certbot.")
        ui.warn(str(e))
        return False

    # Run certbot non-interactively.
    # Note that OCSP stapling support was removed by Let's Encrypt in 2025, so the `--staple-ocsp` and `--must-staple` flags are removed from the below command.
    try:
        subprocess.run(['sudo', 'certbot', '--apache', '--non-interactive', '--agree-tos', '--redirect', '--uir', '--hsts', '-d', ','.join(urls), '--email', email], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error running certbot.")
        ui.warn(str(e))

        # Restore the config file that we replaced in the event of a failure running certbot
        copy_config_with_backup(bak, '/etc/apache2/sites-available/000-default.conf')

        return False
    return True

# Alternate Setup Step 8: Self-Signed Cert Setup
def _08_selfcert_setup():
    fill_urls_var()
    try:
        # Delete existing Certbot Entries
        for url in urls:
            subprocess.run(['sudo', 'certbot', 'delete', '-q', '--cert-name', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Disable the certbot site if enabled from previously being setup with the script
        subprocess.run(['sudo', 'a2dissite', '000-default-le-ssl'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        forget_dns_certificate()
        subprocess.run(['sudo', 'mkdir', '-p', '/etc/apache2/ssl'], check=True)
        # These may be links to a Let's Encrypt certificate from the DNS-challenge option: never write through them
        subprocess.run(['sudo', 'rm', '-f', '/etc/apache2/ssl/wol.key', '/etc/apache2/ssl/wol.crt'], check=True)
        subprocess.run(['sudo', 'openssl', 'req', '-new', '-newkey', 'rsa:4096', '-days', '365', '-nodes', '-x509', '-subj', '/C=AQ/ST=Unlisted/L=Unlisted/O=RWSOLS/CN=%s' % urls[0], '-keyout', '/etc/apache2/ssl/wol.key', '-out', '/etc/apache2/ssl/wol.crt'], check=True)
        subprocess.run(['sudo', 'a2enmod', 'ssl'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'a2enmod', 'headers'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_config_with_backup(script_dir.joinpath('apache2_configs/ssl.conf'), '/etc/apache2/mods-available/ssl.conf')
        copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_self-https.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error creating self-signed key and/or enabling SSL engine.")
        ui.warn(str(e))
        return False
    return True

# Alternate Setup Step 8: A browser-trusted certificate for a LAN name, from Let's Encrypt, using a DNS challenge at Cloudflare (no open port needed)
def _08_dnscert_setup():
    global urls, dns_cert_names
    from rwsols_setup import lancert, wizard
    from rwsols_setup.errors import SetupError
    token = None
    try:
        # Names the LAN's own DNS already gives this Pi are the natural candidates
        helper = wizard.RemoteAccessWizard(ctx, current_mode(), [], get_local_ip() or None)
        candidates = helper._search_domain_candidates() + [n for n in [helper._reverse_dns_candidate()] if n]
        names, email, token = lancert.plan(ctx, candidates, wizard.ddclient_cloudflare_token(ctx.runner))
        ui.say("Requesting the certificate. This takes about a minute...")
        lancert.issue(ctx, names, email, token)
    except SetupError as e:
        ui.warn("The trusted certificate could not be set up.")
        ui.warn(str(e))
        return False
    finally:
        token = None

    try:
        # An older certificate that renews over port 80 would only fail from now on
        if pathlib.Path('/etc/apache2/sites-enabled/000-default-le-ssl.conf').exists():
            old = wizard.ddclient_hosts(ctx.runner)
            if old and ui.confirm("Remove the old Let's Encrypt certificate for " + ", ".join(old) + " (it renews over port 80)?", True):
                for url in old:
                    subprocess.run(['sudo', 'certbot', 'delete', '-q', '--cert-name', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['sudo', 'a2dissite', '000-default-le-ssl'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'a2enmod', 'ssl'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'a2enmod', 'headers'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_config_with_backup(script_dir.joinpath('apache2_configs/ssl.conf'), '/etc/apache2/mods-available/ssl.conf')
        copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_self-https.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("The certificate was issued, but Apache could not be switched over to it.")
        ui.warn(str(e))
        return False
    urls = list(names)
    dns_cert_names = list(names)
    ui.success("https://" + names[0] + " now has a trusted certificate. It renews by itself.")
    ui.hint("If the hostname allowlist is on, add " + names[0] + " to it (the Hostname allowlist section offers it).")
    return True

# Drop the DNS-challenge certificate and its stored API token when another encryption choice replaces it
def forget_dns_certificate():
    if ctx is None:
        return
    from rwsols_setup import lancert
    if lancert.cleanup(ctx):
        ui.say("Removed the DNS-challenge certificate and the API token that was stored for its renewals.")

# Alternate Setup Step 8: Unencrypted Config (We still copy over the 000-default file because it has some security improvements, but the 443 virtual host is ignored because ssl mod is disabled)
def _08_unencrypted_setup():
    fill_urls_var()
    try:
         # Delete existing Certbot Entries
        for url in urls:
            subprocess.run(['sudo', 'certbot', 'delete', '-q', '--cert-name', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Disable the certbot site if enabled from previously being setup with the script
        subprocess.run(['sudo', 'a2dissite', '000-default-le-ssl'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        forget_dns_certificate()
        subprocess.run(['sudo', 'a2dismod', 'ssl'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'a2enmod', 'headers'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_http.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error configuring Apache2.")
        ui.warn(str(e))
        return False
    return True
                
# Step 9: Make some configuration adjustments to the PHP/Apache installation to improve its security
def _09_secure_php():
    # Get Apache's php.ini file location regardless of our PHP version.
    try:
        r = subprocess.run(['sudo', 'find', '/etc/', '-name', 'php.ini'], capture_output=True, check=True)
        php_ini_locations = r.stdout.decode('utf8').splitlines()
        php_ini_apache2_location = None
        for location in php_ini_locations:
            if "apache2" in location:
                php_ini_apache2_location = location
                break
        if php_ini_apache2_location is None:
            ui.warn("Apache2 php.ini could not be located.")
            return False
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/expose_php = On/expose_php = Off/g', php_ini_apache2_location], check=True)  # Note that in PHP7, this should be already disabled, but this ensures that it is.
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/E_ALL & ~E_NOTICE & ~E_STRICT & ~E_DEPRECATED/error_reporting = E_ERROR/g', php_ini_apache2_location], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error updating apache2 php.ini.")
        ui.warn(str(e))
        return False
    try:
        security_conf_location = '/etc/apache2/conf-available/security.conf'
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/ServerSignature On/ServerSignature Off/g', security_conf_location], check=True)
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/ServerTokens OS/ServerTokens Prod/g', security_conf_location], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error updating security.conf.")
        ui.warn(str(e))
        return False
    try:
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error Restarting Apache.")
        ui.warn(str(e))
        return False
    return True
    
# Step 9b: Offer to turn on automatic security updates (unattended-upgrades). Declining is fine and is not asked again.
def _09b_auto_updates():
    from rwsols_setup import hardening
    from rwsols_setup.errors import SetupError
    from rwsols_setup.runtime import SystemRunner
    try:
        hardening.run_auto_updates_step(SystemRunner(), ui)
    except SetupError as e:
        # Not fatal: RWSOLS works without it. Tell the user how to do it by hand or retry.
        ui.warn("Automatic security updates could not be enabled:")
        ui.warn(str(e))
        ui.say("You can set this up yourself with: sudo apt install unattended-upgrades")
        ui.say("To have this script try again, run it again and choose the Security updates section.")
    return True

# Step 10: Generate default config file for WOL page if one doesn't already exist
def _10_prep_config_file():
    if rwsols_config_user.exists():
        ui.say("An existing user config file already exists, so a default one will not be created.")
        return True

    try:
        subprocess.run(['cp', str(rwsols_config_sample), str(rwsols_config_user)], check=True)
    except subprocess.CalledProcessError:
        ui.warn("Error creating default config file.")
        return False

    ui.say("A default WOL config file has been created for you.")
    return True

# Sets $USE_HTTPS in config.php to match how Apache is serving RWSOLS (called by the HTTPS section)
def _set_use_https():
    if not rwsols_config_user.exists():
        ui.warn("Config file couldn't be located for editing!")
        return False
    with open(rwsols_config_user) as f:
        if not any("$USE_HTTPS" in line for line in f):
            ui.warn("The RWSOLS config file is improperly formatted. Consider deleting your config.php file and re-running this script to recreate the default config.")
            return False

    if remote_mode_is_lan_only_listener() and encryption_mode in ("self", "none", "dns"):
        https_rwsols = (encryption_mode != "none") # LAN listener we just configured: no probe needed
    else:
        http_server, http_rwsols, https_server, https_rwsols, https_signed = rwsols_serving_status()
    try:
        subprocess.run(['sudo', 'sed', '-i', 's/.*$USE_HTTPS.*/$USE_HTTPS = ' + ('true' if https_rwsols else 'false') + ';/g', rwsols_config_user], check=True)
    except subprocess.CalledProcessError as e:
        ui.warn("Error setting USE_HTTPS variable.")
        ui.warn(str(e))
        return False
    return True

# True when Apache on this Pi only serves the LAN (LAN only and Cloudflare Tunnel modes)
def remote_mode_is_lan_only_listener():
    return (current_mode() if ctx is not None else remote_mode) != "port_forward"

# Step 11: The passphrase, then the rest of config.php (the list of computers) in a text editor
def _11_modify_config_file():
    if not rwsols_config_user.exists():
        ui.warn("Config file couldn't be located for editing!")
        return False
    with open(rwsols_config_user) as f:
        if not any("$APPROVED_HASH" in line for line in f):
            ui.warn("The RWSOLS config file is improperly formatted. Consider deleting your config.php file and re-running this script to recreate the default config.")
            return False

    # Generate Password Hash for user. If one already appears to be set, ask if they want to change it or leave it.
    ui.say("")
    get_password = False
    with open(rwsols_config_user) as f:
        for line in f:
            if "$APPROVED_HASH" in line:
                if "NULL" in line:
                    get_password = True
                else:
                    pw_option, _ = ui.choose('An existing password is already configured.', ['I would like to keep my currently configured password.','I want to change my password.'])
                    if pw_option == 1:
                        ui.say("Ok, the currently set password will not be changed.")
                    else:
                        get_password = True
                break

    if get_password:
        while True:
            p1 = ui.ask_secret('Please enter the password that you would like to use to access your RWSOLS Web Interface', strip=False)
            p2 = ui.ask_secret('Please re-enter the password to confirm', strip=False)
            if p1 != p2:
                ui.say("The provided passwords do not match. Please try again...\n")
            elif not p1:
                ui.say("The password cannot be empty. Please try again...\n")
            else:
                break

        # We use PHP's password hashing function, since PHP will be responsible for checking the password hash when received by the website
        # The password goes to PHP on standard input: it never appears in the process list, and no character in it can break the command
        try:
            r = subprocess.run(['php', '-r', 'echo password_hash(stream_get_contents(STDIN), PASSWORD_DEFAULT);'], input=p1.encode('utf8'), capture_output=True, check=True)
            pw_hash = r.stdout.decode('utf8').strip()
        except subprocess.CalledProcessError as e:
            ui.warn("Error hashing password.")
            ui.warn(str(e))
            return False

        # Save the Hash, Using the fileinput method here, because sed is a nightmare with escape characters
        # Note that this must be in single quotes so that PHP doesn't expand variables inside the string
        pw_updated = False
        with fileinput.FileInput(rwsols_config_user, inplace = True) as f: 
            for line in f:
                if "$APPROVED_HASH" in line:
                    print('$APPROVED_HASH = \'' + pw_hash + '\';', end="\n")
                    pw_updated = True
                else:
                    print(line, end="")
        if pw_updated:
            ui.say('Password hashed securely and added to configuration file.')
        else:
            ui.warn('The password could not be updated.')
            return False

    # The list of computers and the sleep settings are asked as questions and written to the file
    from rwsols_setup import computers, phpconfig
    try:
        computers.run(ui, phpconfig.ConfigFile(str(rwsols_config_user)))
    except (phpconfig.PhpConfigError, OSError) as e:
        ui.warn("Could not update the config file: " + str(e))
        return False

    ui.explain("Double-check the file (optional)", [
        "Everything is filled in. If you want to look at the result, or change something by hand, the file can open in the nano text editor.",
        "In nano: press Ctrl+O and Enter to save, and Ctrl+X to leave. Leave the settings further down (allowed hosts, tunnel, Access) alone: the other setup sections manage those."])
    if not ui.confirm("Open config.php in the editor to double-check it?", False):
        return True
    try:
        subprocess.run(['nano', str(rwsols_config_user)], check=True)
    except subprocess.CalledProcessError:
        ui.warn("Error editing config file.")
        return False
    ui.say("The config file has been written.\n")
    return True

# Step 12: Copy configured webroot to default apache directory (just run step 3 again)
def _12_copy_webroot():
    return _03_copy_webroot()


# Ensures the urls global list var is populated appropriately
def fill_urls_var():
    global urls
    # If urls variable isn't already filled, but ddclient is running locally, we can check it for the URL, otherwise, we must ask the user.
    if len(urls) == 0:
        try:
            # Copy to tmp file that python has permission to view/parse
            subprocess.run(['sudo', 'cp', str(ddns_real_config), str(ddns_temp_config)], check=True)
            subprocess.run(['sudo', 'chown', os.environ.get('USER'), str(ddns_temp_config)], check=True)
            with open(ddns_temp_config) as f:
                last = None
                for last in (line for line in f if line.rstrip('\n')):
                    pass
            if ',' in last:
                hosts = last.split(",")
                urls = [host.strip() for host in hosts]
            else:
                urls = [last.strip()]
            ui.say("The following URLs will be checked: " + " | ".join(urls))
        except (subprocess.CalledProcessError, OSError, IOError) as e:
            ui.warn("Could not automatically determine the desired URL(s) from ddclient.")
            ui.warn(str(e))
            ui.say("\nIf you ARE running ddclient locally (i.e. Configured by this script), but this failed, it's possible your configuration file is incorrect.")
            ui.say("You can always set it up again by running the Dynamic DNS section of this script.")
            ui.say("Or, edit your ddclient config file manually at " + str(ddns_real_config) + ".\n")
    # If we couldn't grab it from ddclient config file either, then we must ask user
    if len(urls) == 0:
        ui.say("If you're running your DDNS updating service elsewhere, then don't worry about that and enter the URL(s) manually for checking.")
        urls = enter_urls('Enter all the URLs that should be pointing at your public IP (separate with commas if multiple)')

# Prompt Function for entering multiple comma separated, proper-format FQDNs
# query: Request to be presented. e.g. Please enter one or more domain names, comma separated
# Returns a list of validated FQDNs
def enter_urls(query):
    while True:
        v = ui.ask(query)
        if v == "":
            ui.warn("A hostname is required. Please try again.")
            continue
        if ',' in v:
            hosts = v.split(",")
            hosts = [host.strip() for host in hosts]
        else:
            hosts = [v]
        invalid_hosts = []
        for host in hosts:
            if not is_valid_hostname(host):
                ui.warn("'" + host + "' is an invalid hostname.")
                invalid_hosts.append(host)
        if len(invalid_hosts) > 0:
            ui.say("Please try again.\n")
            continue
        else:
            return hosts

# Function for identifying a valid hostname
# https://stackoverflow.com/a/2532344
def is_valid_hostname(hostname):
    if len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1] # strip exactly one dot from the right, if present
    allowed = re.compile("(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))

# Function for determined the default routing IP address of this machine on the local network
# https://stackoverflow.com/a/28950776
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        return False
    finally:
        s.close()
    return ip

# Function that copies relevant apache2 config files while creating timestamped backups of the original
# Returns the path to the backup file that was created
def copy_config_with_backup(src, dst):
    # Make a copy of the destination file with a timestamp
    backup_file_path = str(dst) + '.%s.bak' % str(int(time.time()))
    subprocess.run(['sudo', 'cp', str(dst), backup_file_path], check=True)
    subprocess.run(['sudo', 'cp', str(src), str(dst)], check=True)
    return backup_file_path


# Function that checks to see if RWSOLS is currently serving on port 80 and/or 443, and whether there is already a signed cert.
# Returns a tuple with the following:
#    * boolean: True if there is any server accessible at port 80 (may or may not be RWSOLS)
#    * boolean: True if RWSOLS is serving on Port 80
#    * boolean: True if there is any accessible server at port 443 (may or may not be RWSOLS)
#    * boolean: True if RWSOLS is serving on Port 443 (regardless of cert type)
#    * boolean: True if RWSOLS is serving on Port 443 with a valid, signed cert (False if self-signed cert, or if not found on 443)
def rwsols_serving_status():
    http_server = False
    http_rwsols = False
    https_server = False
    https_rwsols = False
    https_signed = False

    # Load the test page via http on port 80
    try:
        txt = urllib.request.urlopen("http://" + urls[0] + "/knock_knock.txt").read().decode('utf8')
    except ValueError:
        ui.warn("The HTTP URL is of an invalid format.")
    except urllib.error.HTTPError as e:
        ui.warn(f"Couldn't connect to port 80 due to a HTTP Error ({str(e)}).")
        if e.code == 421:
            ui.warn("The hostname allowlist rejected this name. Add " + urls[0] + " to $ALLOWED_HOSTS in config.php.")
    except urllib.error.URLError as e:
        ui.warn(f"Couldn't connect to port 80 due to a URL Error ({str(e)}).")
    else:
        http_server = True
        if txt.startswith("hello?"):
            http_rwsols = True
            ui.success("Your RWSOLS is accessible on port 80!")
        else:
            ui.warn("Something was found at " + urls[0] + " port 80, but it was not RWSOLS.")

    # Load the test page via https on port 443
    try:
        txt = urllib.request.urlopen("https://" + urls[0] + "/knock_knock.txt").read().decode('utf8')
    except ValueError:
        ui.warn("The HTTPS URL is of an invalid format.")
    except urllib.error.HTTPError as e:
        ui.warn(f"Couldn't connect to port 443 due to an HTTP Error ({str(e)}).")
    except (urllib.error.URLError, ssl.SSLCertVerificationError):
        ui.warn("Couldn't to connect to port 443 with strict checking for signed certs.")
        # There was an SSL Cert Error, which means that it still might work with a self-signed cert
        # So, we check again, after allow self-signed certs:
        try:
            self_signed_ssl_context = ssl.create_default_context()
            self_signed_ssl_context.check_hostname=False
            self_signed_ssl_context.verify_mode=ssl.CERT_NONE
            txt = urllib.request.urlopen("https://" + urls[0] + "/knock_knock.txt",  context=self_signed_ssl_context).read().decode('utf8')
        except ValueError:
            ui.warn("The HTTPs URL is of an invalid format.")
        except urllib.error.HTTPError as e:
            ui.warn(f"Couldn't connect to port 443, even when allowing self-signed certs, due to a HTTP Error ({str(e)})")
        except urllib.error.URLError as e:
            ui.warn(f"Couldn't connect to port 443, even when allowing self-signed certs, due to a URL Error ({str(e)}).")
        except ssl.SSLError as e:
            ui.warn("The following SSL error was encountered while trying to check if the server would respond (with self-signed certs allowed):")
            ui.warn(str(e))
        else:
            https_server = True
            if txt.startswith("hello?"):
                https_rwsols = True
                ui.success("Your RWSOLS is accessible on port 443, but it is using a self-signed SSL certificate!")
            else:
                ui.warn("Something was found at " + urls[0] + " port 443, but it was not RWSOLS.")
    except ssl.SSLError as e:
            ui.warn("The following SSL error was encountered while trying to verify the server:")
            ui.warn(str(e))
    else:
        https_server = True
        https_signed = True
        if txt.startswith("hello?"):
            https_rwsols = True
            ui.success("Your RWSOLS is accessible on port 443 and is using a properly signed SSL certificate!")
        else:
            ui.warn("Something was found at " + urls[0] + " port 443, but it was not RWSOLS.")

    
    return http_server, http_rwsols, https_server, https_rwsols, https_signed

# Function that returns true if two directories have matching contents
def directories_match(dir1, dir2):
    if not os.path.isdir(dir1) or not os.path.isdir(dir2):
        raise ValueError("Both inputs must be valid directories.")

    # Compared as root: the served config.php is not readable by ordinary users.
    r = subprocess.run(['sudo', 'diff', '-rq', str(dir1), str(dir2)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0

# Execute
if __name__ == "__main__":
    main()
