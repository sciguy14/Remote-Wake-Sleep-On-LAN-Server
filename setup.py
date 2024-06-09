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
import getpass
import socket
import time
import ssl
import fileinput

# Exit Handler
def signal_handler(sig, frame):
    print('\n\nABORTING SCRIPT.\n')
    sys.exit(1)
signal.signal(signal.SIGINT, signal_handler)

# Global Variables
script_dir = pathlib.Path(__file__).parent.absolute()
public_ipv4 = "127.0.0.1"
urls = []
encryption_mode="none" # Options: "none","self","certbot","skip"
ddns_temp_config=pathlib.Path('/tmp/ddclient.conf')
ddns_real_config=pathlib.Path('/etc/ddclient.conf')
rwsols_config_sample = script_dir.joinpath('www/html/config_sample.php')
rwsols_config_user = script_dir.joinpath('www/html/config.php')

# Colors
black = lambda text: '\033[0;30m' + text + '\033[0m'
red = lambda text: '\033[0;31m' + text + '\033[0m'
green = lambda text: '\033[0;32m' + text + '\033[0m'
yellow = lambda text: '\033[0;33m' + text + '\033[0m'
blue = lambda text: '\033[0;34m' + text + '\033[0m'
magenta = lambda text: '\033[0;35m' + text + '\033[0m'
cyan = lambda text: '\033[0;36m' + text + '\033[0m'
white = lambda text: '\033[0;37m' + text + '\033[0m'

# Main Logic
def main():
    # Intro
    print("")
    print(cyan(">>>                                                       <<<"))
    print(cyan(">>> Remote-Wake-Sleep-On-LAN-Server (RWSOLS) Setup Script <<<"))
    print(cyan(">>>                                                       <<<\n"))

    # Warn the user that internet scripts are dangerous
    print(yellow("This script will install the required packages and configure permissions as necessary for RWSOLS to work properly. It assumes you are on the bookworm release."))
    print(yellow("As with all scripts that you find on the internet, I strongly suggest you read its contents before blindly executing it."))
    print(yellow("This script assumes your user is a passwordless sudo user, which is the default for the 'pi' user on Raspbian. It executes commands with sudo.\n"))
    print(yellow("If you want to abort this script to inspect its contents, press Ctrl+C now."))
    input(yellow("If you have read and understood what this script is doing, Press Enter to continue with the automatic setup."))
    print("")

    # Add note about skipping completed steps
    print("Note: One-time steps that have already been executed will be automatically skipped.")
    print("If you need to re-run them, delete the relevant dot files in this folder.\n")
    time.sleep(1)

    # Install prerequisites
    run_step('_01_install_prereqs', 'Install Prequisite Software')

    # Ping Permissions
    run_step('_02_ping_permissions', 'Grant Ping Permission')

    # Symlink the Webroot to our files
    run_step('_03_symlink_webroot', 'Symlink Apache2 Webroot')

    # ddclient DDNS Setup
    run_step('_04_setup_ddns', 'Setup DDNS')

    # Get Public IPv4 Address
    run_step('_05_get_ip', 'Get Public IPv4 Address', False)

    # Confirm functional URL
    run_step('_06_check_urls', 'Check DNS URL Resolution', False)

    # Check if our server is up and serving content on port 80
    run_step('_07_server_check', 'Check if RWSOLS is accessible on port 80 and/or port 443', False)

    # Setup requested encryption options
    if encryption_mode == "certbot":
        run_step('_08_certbot_setup', 'Certbot-Signed SSL Certificate Setup', False)
    elif encryption_mode == "self":
        run_step('_08_selfcert_setup', 'Self-Signed SSL Certificate Setup', False)
    elif encryption_mode == "none":
        run_step('_08_unencrypted_setup', 'Unencrypted Setup', False)
    else:
        print("No Apache2 encryption setting changes will be made.\n")

    # Security-Harden PHP installation
    run_step('_09_secure_php', 'Secure the PHP Installation')

    # Create user copy of config file
    run_step('_10_prep_config_file', 'Generate Default WOL Config File', False)

    # Create user copy of config file
    run_step('_11_modify_config_file', 'Modify WOL Config File', False)


    print("Automatic Setup is now complete.")


# Function for encapsulating parts of the setup process that can be skipped on script re-run
# handle: short name for setup step
# description: human-understandable setup setup name
# dot_file_skippable: If True, this step will be skipped if already completed in previous run
# Returns True if the setup step should be run, False if it was already previously completed
def run_step(handle, description, dot_file_skippable=True):
    if script_dir.joinpath("." + handle).exists() and dot_file_skippable:
        print(blue("'" + description + "' has already been completed. Skipping.\n"))
    else:
        print(cyan("Running: ") + description)
        if eval(handle + '()'):
            print(green("Done: ") + description + "\n")
            if dot_file_skippable:
                script_dir.joinpath("." + handle).touch()
        else:
            print(red("Script Exiting due to failure in '" + description + "' setup step.\n"))
            sys.exit(1) 

# Setup Step 1: Install Prequisite Software
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
    except subprocess.CalledProcessError as e:
        print(yellow("Error installing prerequisite software."))
        return False
    return True

# Setup Step 2: The PHP server uses the built-in ping command to check if the remote machine is awake or not. Give all users on the pi permission to ping.
def _02_ping_permissions():
    try:
        subprocess.run(['sudo', 'chmod', 'u+s', '/usr/bin/ping'], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error setting ping permissions."))
        return False
    return True

# Setup Step 3: Symlink Apache2 Webroot
def _03_symlink_webroot():
    try:
        apache_www_dir = pathlib.Path('/var/www')
        wol_www_dir = script_dir.joinpath('www')
        if apache_www_dir.exists():
            # If there is a directory here that isn't symlinked, we back it up.
            if not apache_www_dir.is_symlink():
                subprocess.run(['sudo', 'mv', str(apache_www_dir), str(apache_www_dir) + "_backup"], check=True)
        subprocess.run(['sudo', 'ln', '-sf', str(wol_www_dir), str(apache_www_dir)], check=True)
    except Exception as e:
        print(yellow("Error symlinking apache webroot to local folder."))
        print(yellow(str(e)))
        return False
    return True

# Setup Step 4: DDNS Configuration
def _04_setup_ddns():
    ddns_option, _ = multi_choice('Will you handle Dynamic DNS updates from your router, or would you like to setup Dynamic DNS updating on this Pi?', ['I will handle DDNS on my router or elesewhere.','I want to configure DDNS updates from this Pi.'])
    print ("")
    if ddns_option == 1:
        print("Ok. If you haven't already, go configure your router or other device to update your DDNS service of choice, now.")
        input("Once you've done that, press Enter to proceed, and the URL/IP will be checked.")
        return True
    elif ddns_option == 2:
        # Setup ddclient so that our URL points to our public IP. If we don't do this, certbot won't be able to complete the port 80 challenge.

        # Note, we assume user is on at least bookworm release, as that has version 3.10 of ddclient
        input("ddclient will be installed via apt package manager. Snap version will be removed if present. Press enter to proceed.")
        print("")
        print("Installing ddclient and required packages...")
        try:
            subprocess.run(['sudo', 'snap', 'remove', '--purge', 'ddclient-snap']) # Remove any existing installations from snap
            subprocess.run(['sudo', 'apt', 'install', '-y', 'libio-socket-ssl-perl', 'ddclient'], check=True)
        except subprocess.CalledProcessError as e:
            print(yellow("Error installing ddclient and/or libio-socket-ssl-perl."))
            print(yellow(str(e)))
            return False


        print("\nNow we need to setup ddclient on this device.")
        ddclient_config_option, _ = multi_choice("Would you like to use this script's wizard to help you generate your ddclient config file, or would you like to provide your own config file?", ['Please help me generate the config file.','I will manually create the ddclient config file (advanced!).'])
        if ddclient_config_option == 1:
            print("\nGreat! Answer the following questions to generate your config file. You'll have a chance to review it and re-answer if you make a mistake.")
            # Now, get the info necessary to create the ddclient.conf file
            while(True):
                with open(ddns_temp_config, 'w') as f:
                    f.write('# Generated by RWSOLS Setup Script: https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server\n')
                    f.write('use=web\n') # We are running this behind a router, so we must use the web method to get the IP
                    f.write('ssl=yes\n') # Don't send our password in cleartext!
                    i, v = multi_choice('Choose a DDNS Protocol.', ['dyndns2','noip','googledomains','cloudflare','namecheap','zoneedit1','easydns','nfsn','yandex','ovh','dinahosting'])
                    f.write('protocol=' + v + '\n')
                    while(True):
                        v = input('Enter your Server URL (ex: members.dyndns.org). Leave blank if not needed by your service/protocol: ')
                        if v == "":
                            break # No Server value provided
                        elif is_valid_hostname(v):
                            f.write('server=' + v + '\n')
                            break
                        else:
                            print ("'" + v + "' is not a valid server. Please try again.")
                    v = input('Enter your login username for the service: ')
                    f.write('login=' + v + '\n')
                    v = getpass.getpass('Enter your login password for the service: ')
                    f.write('password=' + v + '\n')
                    global urls
                    urls = enter_urls('Enter all your Hostname(s) to update (ex: myhome.com)(multi ex: sub1.myhome.com, myhome.com): ')
                    f.write(",".join(urls) + '\n')
                print("\nYour config file contents will be written as follows:")
                with open(ddns_temp_config, 'r') as f:
                    for line in f:
                            print("\t" + line.strip())
                    config_okay, _ = multi_choice('Does this look okay?',['Yes Please write this configuration file', 'No. I want to do the wizard again.'])
                    if config_okay == 1:
                        break
                    else:
                        print("\nOkay. The wizard will run again.\n")
            try:
                subprocess.run(['sudo', 'cp', str(ddns_temp_config), str(ddns_real_config)], check=True)
            except subprocess.CalledProcessError as e:
                print(yellow("Error copying config file."))
                return False
        elif ddclient_config_option == 2:
            print("Okay. It recommend that you set use=web and ssl=yes. When you're done editing, save and exit nano to proceed.")
            input("Press enter to open the config file with nano.")
            try:
                subprocess.run(['sudo', 'nano', str(ddns_real_config)], check=True)
            except subprocess.CalledProcessError as e:
                print(yellow("Error editing config file."))
                return False
            print("The config file has been written.\n")

        print("Launching ddclient daemon...")
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'ddclient'], check=True)
        except subprocess.CalledProcessError as e:
            print(yellow("Error launching ddclient daemon."))
            return False

        print("Waiting 5 seconds for DNS update to execute before proceeding...")
        time.sleep(5)    
        return True

# Setup Step 5: Get Public IP -  we'll need it for checking existing DDNS configs, or for configuring a new one
def _05_get_ip():
    global public_ipv4
    try:
        public_ipv4 = urllib.request.urlopen('https://v4.ident.me').read().decode('utf8')
    except urllib.error.URLError as e:
        if hasattr(e, 'reason'):
            print(yellow('Failed to reach the ident.me server.'))
            print(yellow('Reason: ' + str(e.reason)))
        elif hasattr(e, 'code'):
            print(yellow('The ident.me server couldn\'t fulfill the request.'))
            print(yellow('Error code: ' + str(e.code)))
        return False
    print("The public-facing IPv4 address of this Pi's network was detected as " + cyan(public_ipv4) + ".")
    return True

# Setup Step 6: Confirm that the DNS update suceeded
def _06_check_urls():
    fill_urls_var()
    max_attempts = 5
    backoff_exponent = 2

    # Now we check to see if each of the URLs resolves to our public-facing IP address.
    failures = False
    for url in urls:
        try:
            resolved_ip = socket.gethostbyname(url)
        except socket.gaierror as e:
            print(yellow("Failed to resolve " + url + "! Is it a valid URL?"))
            failures = True
        else:
            attempt = 1
            for attempt in range(1, max_attempts+1):
                if resolved_ip != public_ipv4:
                    if attempt < max_attempts:
                        print("[Attempt " + str(attempt) + "/" + str(max_attempts) + "] " + yellow(url + " resolves to " + resolved_ip + " instead of detected public IP of " + public_ipv4 + "! ") + "Checking again in " + str(attempt**backoff_exponent) + " seconds.")
                        time.sleep(attempt**backoff_exponent)
                    else:
                        print("[Attempt " + str(attempt) + "/" + str(max_attempts) + "] " + yellow(url + " resolves to " + resolved_ip + " instead of detected public IP of " + public_ipv4 + "! DDNS configured incorrectly, or it hasn't updated yet."))
                        failures = True
                else:
                    print("[Attempt " + str(attempt) + "/" + str(max_attempts) + "] " + cyan(url) + " correctly resolves to " + cyan(public_ipv4) + "!")
                    break
            else:
                print(yellow(url + " resolves to " + resolved_ip + " instead of detected public IP of " + public_ipv4 + "! "))
    if failures:
        print(yellow("One or more of your URLs failed to resolve! Try running this script again after checking your domains and your DDNS configuration."))
        print(yellow("Delete the setup_ddns dot file in this directory if you need to reconfigure local DDNS updating."))
        return False
    return True

# Setup Step 7: Check to see if port 80 and/or port 443 is currently serving RWSOLs to correctly display options for cert setup
def _07_server_check():
    global encryption_mode
    no_encryption_option_text = 'I do not wish to enable encryption. (You can still manually setup certbot later using a Port 80 or DNS challenege).'
    disable_encryption_option_text = 'I wish to disable encryption, making RWSOLS accessible over an unencrypted connection only.'
    switch_to_unsigned_option_text = 'I wish to change from a signed certificate to an unsigned certificate.'
    switch_to_signed_option_text = 'I wish to change from an unsigned certificate to a signed certificate (RECOMMENDED OPTION).'
    retain_encryption_settings_option_text = 'I wish to retain the existing encryption settings.'
    certbot_encryption_option_text = 'Proceed with automatic certbot signed encryption setup (STRONGLY RECOMMENDED OPTION)'
    self_signed_encryption_option_text = 'I would like this script to automatically configure encryption using a self-signed certificate.'
    exit_and_fix_option_text = 'Please exit the script - I will forward port 80 and run the script again once I have done that.'
    
    http_server, http_rwsols, https_server, https_rwsols, https_signed = rwsols_serving_status()
    print("")

    # RWSOLS is already running with a signed cert
    if https_rwsols and https_signed:
        print("Your RWSOLS is already acceessible over an encrypted connection with a valid signed certificate.")
        c, _ = multi_choice('How would you like to proceed?', [retain_encryption_settings_option_text, switch_to_unsigned_option_text, disable_encryption_option_text])
        if c == 1:
            encryption_mode="skip"
        elif c == 2:
            encryption_mode="self"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is already running with a self-signed cert, and port 80 is accessible
    elif http_rwsols and https_rwsols and not https_signed:
        print("Your RWSOLS is already acceessible over an encrypted connection, but it is using a self-signed certificate.")
        print("Port 80 is also correctly resolving to RWSOLS, so automatic certbot configuration should be possible, and is recommended.")
        c, _ = multi_choice('How would you like to proceed?', [switch_to_signed_option_text, retain_encryption_settings_option_text, disable_encryption_option_text])
        if c == 1:
            encryption_mode="certbot"
        elif c == 2:
            encryption_mode="skip"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is already running with a self-signed cert and port 80 is not accessible
    elif not http_rwsols and https_rwsols and not https_signed:
        print("Your RWSOLS is already acceessible over an encrypted connection, but it is using a self-signed certificate.")
        print("Port 80 does not appear to be open and pointed to RWSOLS, so automatic certbot configuration is not possible.")
        c, _ = multi_choice('How would you like to proceed?', [exit_and_fix_option_text, retain_encryption_settings_option_text, disable_encryption_option_text])
        if c == 1:
            local_ip = get_local_ip()
            if local_ip:
                print("\nOk! Forward ports 80 and 443 to this Pi (your Pi's local IP that you should forward to is " + magenta(local_ip) + ").")
            return False
        elif c == 2:
            encryption_mode="skip"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is running on port 80, but doesn't have encryption setup
    elif http_rwsols:
        print("It looks like RWSOLS is active on port 80, but HTTPS is not configured.")
        print("Certbot should be able to automatically issue and configure a signed, auto-renewing encryption certificate.")
        c, _ = multi_choice('How would you like to proceed?', [certbot_encryption_option_text, self_signed_encryption_option_text, no_encryption_option_text])
        if c == 1:
            encryption_mode="certbot"
        elif c == 2:
            encryption_mode="self"
        else:
            encryption_mode="none"
        return True

    # RWSOLS is not currently accessible
    else:
        print("In order to automatically configure SSL Encryption, port 80 must be open on your router and forwarded to port 80 on this Pi.")
        print("If you are unable to forward port 80 because you are using it for another service, or because your ISP blocks it, you can manually configure certbot to perform a DNS challenge instead, or this script can setup unsigned encryption.")
        c, _ = multi_choice('How would you like to proceed?', [exit_and_fix_option_text , self_signed_encryption_option_text, no_encryption_option_text])
        if c == 1:
            local_ip = get_local_ip()
            if local_ip:
                print("\nOk! Forward ports 80 and 443 to this Pi (your Pi's local IP that you should forward to is " + magenta(local_ip) + ").")
            return False
        elif c == 2:
            encryption_mode="self"
        else:
            encryption_mode="none"
        return True


# Setup Step 8: Certbot config
def _08_certbot_setup():
    # Let's Encrypt!
    print("A valid email address is required so that the Let's Encrypt Certificate Authority can email you if there is problem with the automatic renewal of your certificate.")
    print("This email will NOT be signed up for a mailing list.")
    while True:
        email = input("Please provide a valid email: ")
        if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", email):
            print(yellow("That doesn't look like a valid email..."))
        else:
            break
    fill_urls_var()

    # In case setup has previously run and has copied over the site config with the SSL port configured, we restore the the HTTP-only config so that there is only one virtualhost for certbot
    try:
        bak = copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_http.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error getting Apache2 site config into deafult state before running certbot."))
        print(yellow(str(e)))
        return False

    # Run certbot non-interactively
    try:
        subprocess.run(['sudo', 'certbot', '--apache', '--non-interactive', '--agree-tos', '--redirect', '--uir', '--hsts', '--staple-ocsp', '--must-staple', '-d', ','.join(urls), '--email', email], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error running certbot."))
        print(yellow(str(e)))

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

        subprocess.run(['sudo', 'mkdir', '-p', '/etc/apache2/ssl'], check=True)
        subprocess.run(['sudo', 'openssl', 'req', '-new', '-newkey', 'rsa:4096', '-days', '365', '-nodes', '-x509', '-subj', '/C=AQ/ST=Unlisted/L=Unlisted/O=RWSOLS/CN=%s' % urls[0], '-keyout', '/etc/apache2/ssl/wol.key', '-out', '/etc/apache2/ssl/wol.crt'], check=True)
        subprocess.run(['sudo', 'a2enmod', 'ssl'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'a2enmod', 'headers'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_config_with_backup(script_dir.joinpath('apache2_configs/ssl.conf'), '/etc/apache2/mods-available/ssl.conf')
        copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_self-https.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error creating self-signed key and/or enabling SSL engine."))
        print(yellow(str(e)))
        return False
    return True

# Alternate Setup Step 8: Unencrypted Config (We still copy over the 000-default file because it has some security improvements, but the 443 virtual host is ignored because ssl mod is disabled)
def _08_unencrypted_setup():
    fill_urls_var()
    try:
         # Delete existing Certbot Entries
        for url in urls:
            subprocess.run(['sudo', 'certbot', 'delete', '-q', '--cert-name', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Disable the certbot site if enabled from previously being setup with the script
        subprocess.run(['sudo', 'a2dissite', '000-default-le-ssl'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(['sudo', 'a2dismod', 'ssl'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['sudo', 'a2enmod', 'headers'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_config_with_backup(script_dir.joinpath('apache2_configs/000-default_http.conf'), '/etc/apache2/sites-available/000-default.conf')
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error configuring Apache2."))
        print(yellow(str(e)))
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
            print(yellow("Apache2 php.ini could not be located."))
            return False
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/expose_php = On/expose_php = Off/g', php_ini_apache2_location], check=True)  # Note that in PHP7, this should be already disabled, but this ensures that it is.
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/E_ALL & ~E_NOTICE & ~E_STRICT & ~E_DEPRECATED/error_reporting = E_ERROR/g', php_ini_apache2_location], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error updating apache2 php.ini."))
        print(yellow(str(e)))
        return False
    try:
        security_conf_location = '/etc/apache2/conf-available/security.conf'
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/ServerSignature On/ServerSignature Off/g', security_conf_location], check=True)
        subprocess.run(['sudo', 'sed', '-i.%s.bak' % str(int(time.time())), 's/ServerTokens OS/ServerTokens Prod/g', security_conf_location], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error updating security.conf."))
        print(yellow(str(e)))
        return False
    try:
        subprocess.run(['sudo', 'service', 'apache2', 'restart'], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error Restarting Apache."))
        print(yellow(str(e)))
        return False
    return True
    
# Step 10: Generate default config file for WOL page if one doesn't already exist
def _10_prep_config_file():
    if rwsols_config_user.exists():
        print("An existing user config file already exists, so a default one will not be created.")
        return True

    try:
        subprocess.run(['cp', str(rwsols_config_sample), str(rwsols_config_user)], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error creating default config file."))
        return False

    print("A default WOL config file has been created for you.")
    return True

# Step 11: Help with editing of the config file
def _11_modify_config_file():
    if not rwsols_config_user.exists():
        print(yellow("Config file couldn't be located for editing!"))
        return False

    # Check for the variables we are going to edit
    use_https_found = False
    approved_hash_found = False
    with open(rwsols_config_user) as f:
        for line in f:
            if "$APPROVED_HASH" in line:
                approved_hash_found = True
            if "$USE_HTTPS" in line:
                use_https_found = True
    if not use_https_found or not approved_hash_found:
        print(yellow("The RWSOLS config file is improperly formatted. Consider deleting your config.php file and re-running this script to recreate the default config."))
        return False


    # If we're encrypted, pre-set that value correctly in the config file.
    http_server, http_rwsols, https_server, https_rwsols, https_signed = rwsols_serving_status()
    if https_rwsols:
        try:
            subprocess.run(['sudo', 'sed', '-i', 's/.*$USE_HTTPS.*/$USE_HTTPS = true;/g', rwsols_config_user], check=True)
        except subprocess.CalledProcessError as e:
            print(yellow("Error setting USE_HTTPS variable."))
            print(yellow(str(e)))
            return False
    else:
        try:
            subprocess.run(['sudo', 'sed', '-i', 's/.*$USE_HTTPS.*/$USE_HTTPS = false;/g', rwsols_config_user], check=True)
        except subprocess.CalledProcessError as e:
            print(yellow("Error setting USE_HTTPS variable."))
            print(yellow(str(e)))
            return False

    # Generate Password Hash for user. If one already appears to be set, ask if they want to change it or leave it.
    print("")
    get_password = False
    with open(rwsols_config_user) as f:
        for line in f:
            if "$APPROVED_HASH" in line:
                if "NULL" in line:
                    get_password = True
                else:
                    pw_option, _ = multi_choice('An existing password is already configured.', ['I would like to keep my currently configured password.','I want to change my password.'])
                    if pw_option == 1:
                        print("Ok, the currently set password will not be changed.")
                    else:
                        get_password = True
                break

    if get_password:
        while True:
            p1 = getpass.getpass('Please enter the password that you would like to use to access your RWSOLS Web Interface: ')
            p2 = getpass.getpass('Please re-enter the password to confirm: ')
            if p1 != p2:
                print("The provided passwords do not match. Please try again...\n")
            else:
                break

        # We use PHP's password hashing function, since PHP will be responsible for checking the password hash when received by the website
        # Note that this must be in single quotes so that PHP doesn't expand variables inside of the string
        try:
            r = subprocess.run(['php', '-r', 'echo password_hash(\'' + p1 + '\', PASSWORD_DEFAULT);'], capture_output=True, check=True)
            pw_hash = r.stdout.decode('utf8').strip()
        except subprocess.CalledProcessError as e:
            print(yellow("Error hashing password."))
            print(yellow(str(e)))
            return False

        # Save the Hash, Using the fileinput method here, because sed is a nightmare with escape characters
        # Note that this must be in single quotes so that PHP doesn't expand variables inside of the string
        pw_updated = False
        with fileinput.FileInput(rwsols_config_user, inplace = True) as f: 
            for line in f:
                if "$APPROVED_HASH" in line:
                    print('$APPROVED_HASH = \'' + pw_hash + '\';', end="\n")
                    pw_updated = True
                else:
                    print(line, end="")
        if pw_updated:
            print('Password hashed securely and added to configuration file.')
        else:
            print(yellow('The password could not be updated.'))
            return False

    print("\nFor the remainder of the configuration, it's easiest for you to edit the file directly.")
    input("Press enter to open the config file with nano for editing so you can input your values. Save and exit when done.")
    try:
        subprocess.run(['nano', str(rwsols_config_user)], check=True)
    except subprocess.CalledProcessError as e:
        print(yellow("Error editing config file."))
        return False
    print("The config file has been written.\n")
    return True



# Prompt Function for Multiple Choice Questions.
# query: question string to be asked. e.g. "What is the best sandwich ingredient?"
# options: list of possible choices. e.g. ['bacon', 'lettuce', 'tomato']
# Returns the 1-indexed selection number (1 if you choose bacon), and the value as a tuple (bacon)
def multi_choice(query, options):
    while True:
        print(query)

        for i in range(len(options)):
            if i >=9:
                print(" " + str(i+1) + ": " + options[i])
            else:
                print(" " + str(i+1) + ":  " + options[i])
            

        try:
            choice = int(input("Choose an number from the list (1-" + str(len(options))  + "): "))
            if choice in range(1, len(options)+1):
                return choice, options[choice-1]
            else:
                raise ValueError
        except ValueError:
            print(yellow("Please choose a number from the list!\n"))

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
            print("The following URLs will be checked: " + " | ".join(urls))
        except (subprocess.CalledProcessError, OSError, IOError) as e:
            print(yellow("Could not automatically determine the desired URL(s) from ddclient."))
            print(yellow(str(e)))
            print("\nIf you ARE running ddclient locally (i.e. Configured by this script), but this failed, it's possible your configuration file is incorrect.")
            print("You can always re-run that step by delecting the 'setup_ddns' dot file in this folder and re-running this script.")
            print("Or, edit your ddclient config file manually at " + str(ddns_real_config) + ".\n")
    # If we couldn't grab it from ddclient config file either, then we must ask user
    if len(urls) == 0:
        print("If you're running your DDNS updating service elsewhere, then don't worry about that and enter the URL(s) manually for checking.")
        urls = enter_urls('Enter all the URLs that should be pointing at your public IP (seperate with commas if multiple): ')

# Prompt Function for entering multiple comma seperated, proper-format FQDNs
# query: Request to be presented. e.g. Please enter one or more domain names, comma seperated
# Returns a list of validated FQDNs
def enter_urls(query):
    while(True):
        v = input(query)
        if v == "":
            print(yellow("A hostname is required. Please try again."))
            continue
        if ',' in v:
            hosts = v.split(",")
            hosts = [host.strip() for host in hosts]
        else:
            hosts = [v]
        invalid_hosts = []
        for host in hosts:
            if not is_valid_hostname(host):
                print(yellow("'" + host + "' is an invalid hostname."))
                invalid_hosts.append(host)
        if len(invalid_hosts) > 0:
            print("Please try again.\n")
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
    # Make a copy of the destination file with a timesamp
    backup_file_path = str(dst) + '.%s.bak' % str(int(time.time()))
    subprocess.run(['sudo', 'cp', str(dst), backup_file_path], check=True)
    subprocess.run(['sudo', 'cp', str(src), str(dst)], check=True)
    return backup_file_path


# Function that checks to see if RWSOLS is current servering on port 80 and/or 443, and whether or not there is already a signed cert.
# Returns a tuple with the following:
#    * boolean: True if there is any accessible at port 80 (may or may not be RWSOLS)
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
    except ValueError as e:
        print(yellow("The HTTP URL is of an invalid format."))
    except urllib.error.HTTPError as e:
        print(yellow(f"Couldn't connect to port 80 due to a HTTP Error ({str(e)})."))
    except urllib.error.URLError as e:
        print(yellow(f"Couldn't connect to port 80 due to a URL Error ({str(e)})."))
    else:
        http_server = True
        if txt.startswith("hello?"):
            http_rwsols = True
            print(magenta("Your RWSOLS is accessible on port 80!"))
        else:
            print(yellow("Something was found at " + urls[0] + " port 80, but it was not RWSOLS."))

    # Load the test page via https on port 443
    try:
        txt = urllib.request.urlopen("https://" + urls[0] + "/knock_knock.txt").read().decode('utf8')
    except ValueError as e:
        print(yellow("The HTTPS URL is of an invalid format."))
    except urllib.error.HTTPError as e:
        print(yellow(f"Couldn't connect to port 443 due to an HTTP Error ({str(e)})."))
    except (urllib.error.URLError, ssl.SSLCertVerificationError) as e:
        print(yellow("Couldn't to connect to port 443 with strict checking for signed certs."))
        # There was a SSL Cert Error, which means that it still might work with a self-signed cert
        # So, we check again, after allow self-signed certs:
        try:
            self_signed_ssl_context = ssl.create_default_context()
            self_signed_ssl_context.check_hostname=False
            self_signed_ssl_context.verify_mode=ssl.CERT_NONE
            txt = urllib.request.urlopen("https://" + urls[0] + "/knock_knock.txt",  context=self_signed_ssl_context).read().decode('utf8')
        except ValueError as e:
            print(yellow("The HTTPs URL is of an invalid format."))
        except urllib.error.HTTPError as e:
            print(yellow(f"Couldn't connect to port 443, even when allowing self-signed certs, due to a HTTP Error ({str(e)})"))
        except urllib.error.URLError as e:
            print(yellow(f"Couldn't connect to port 443, even when allowing self-signed certs, due to a URL Error ({str(e)})."))
        except ssl.SSLError as e:
            print(yellow("The following SSL error was encountered while trying to check if the server would respond (with self-signed certs allowed):"))
            print(yellow(str(e)))
        else:
            https_server = True
            if txt.startswith("hello?"):
                https_rwsols = True
                print(magenta("Your RWSOLS is accessible on port 443, but it is using a self-signed SSL certificate!"))
            else:
                print(yellow("Something was found at " + urls[0] + " port 443, but it was not RWSOLS."))
    except ssl.SSLError as e:
            print(yellow("The following SSL error was encountered while trying to verify the server:"))
            print(yellow(str(e)))
    else:
        https_server = True
        https_signed = True
        if txt.startswith("hello?"):
            https_rwsols = True
            print(magenta("Your RWSOLS is accessible on port 443 and is using a properly signed SSL certificate!"))
        else:
            print(yellow("Something was found at " + urls[0] + " port 443, but it was not RWSOLS."))

    
    return http_server, http_rwsols, https_server, https_rwsols, https_signed 



# Execute
if __name__ == "__main__":
    main()
