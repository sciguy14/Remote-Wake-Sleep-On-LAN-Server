<?php

/*
Remote Wake/Sleep-On-LAN Server [CONFIGURATION FILE]
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
Original Author: Jeremy E. Blum (https://www.jeremyblum.com)
Security Edits By: Felix Ryan (https://www.felixrr.pro)
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)
*/

// The values in this section are automatically set by the RWSOLS setup script - you shouldn't need to change them
// -------------------------------
$USE_HTTPS = false;
$APPROVED_HASH = NULL;
// -------------------------------

// >
// >>
// >>>
// >>
// >

// The rest of the values describe the computers to wake/sleep. The setup script asks for them and fills them in (section "Passphrase and computers");
// you can also edit them here by hand.
// Note that this file (and everything else in this folder) needs to be copied into /var/www/html, as that if where apache's webroot is.
// The Setup script will automatically copy this file over at completion, and you can run it again if needed.
// -------------------------------

// This is the number of times that the WOL server will try to ping the target computer to check if it has woken up. Default = 15.
$MAX_PINGS = 15;

// This is the number of seconds to wait between pings commands when waking up or sleeping. Waking from shutdown or sleep will impact this.
$SLEEP_TIME = 5;

// This is the Name of the computers to appear in the drop down
$COMPUTER_NAME = array("computer1","computer2");

// This is the MAC address of the Network Interface on the computer you are trying to wake.
$COMPUTER_MAC = array("00:00:00:00:00:00","00:00:00:00:00:00");

// This is the LOCAL IP address of the computer you are trying to wake.  Use a reserved DHCP through your router's administration interface to ensure it doesn't change.
$COMPUTER_LOCAL_IP = array("192.168.0.1","192.168.0.2");

// This is the Port being used by the Windows SleepOnLan Utility to initiate a Sleep State
// http://www.ireksoftware.com/SleepOnLan/
// Alternate Download Link: http://www.jeremyblum.com/wp-content/uploads/2013/07/SleepOnLan.zip
$COMPUTER_SLEEP_CMD_PORT = 7760;

// Command to be issued by the windows sleeponlan utility
// options are suspend, hibernate, logoff, poweroff, forcepoweroff, lock, reboot
// You can create a windows scheduled task that starts sleeponlan.exe on boot with following startup parameters /auto /port=7760
$COMPUTER_SLEEP_CMD = "suspend";

// This is the location of the bootstrap style folder relative to your index and config file. Default = "" (Same folder as this file)
// Directory must be called "bootstrap". You may wish to move if this WOL script is the "child" of a larger web project on your Pi, that will also use bootstrap styling.
// If if it on directory up, for example, you would set this to "../"
// Two directories up? Set too "../../"
// etc...
$BOOTSTRAP_LOCATION_PREFIX = "";
// -------------------------------

// Remote access / security (optional)
// The setup wizard normally fills these in. Leaving them unset (or deleting them) keeps the previous behavior.
// -------------------------------

// Exact host names this server answers to, e.g. array("wol.example.com", "raspberrypi.local", "192.168.1.10").
// Protects against DNS rebinding (a malicious web page pointing its own domain at your Pi's address).
// No wildcards or suffix matching; a bare IP must be listed to be accepted. Default = array() (no Host checking).
$ALLOWED_HOSTS = array();

// When $ALLOWED_HOSTS is in use, also accept localhost, 127.0.0.1 and [::1]. Default = false.
$ALLOW_LOCALHOST = false;

// Write a rate-limited syslog line (client IP and Host) for each request rejected by $ALLOWED_HOSTS. Default = false.
$LOG_HOST_REJECTIONS = false;

// Public hostname served through Cloudflare Tunnel: the one address you type to reach RWSOLS from outside your home.
// Normally a subdomain of your domain, e.g. "rwsols.example.com" when your domain is example.com. Only this name is handed to the tunnel;
// the domain itself (example.com) and its other subdomains are not touched and stay free for dynamic DNS, a website or other services.
// It must also be listed in $ALLOWED_HOSTS. Default = NULL.
$TUNNEL_HOSTNAME = NULL;

// Loopback Apache listener that cloudflared forwards to. Used only by the setup wizard. Defaults = "127.0.0.1" and 8080.
$TUNNEL_ORIGIN_BIND = "127.0.0.1";
$TUNNEL_ORIGIN_PORT = 8080;

// Cloudflare Access: team domain without scheme (e.g. "example.cloudflareaccess.com") and the Access application AUD tag.
// When both are set, tunnel requests must carry a valid Access token. Setting only one denies all tunnel requests. Defaults = NULL.
$ACCESS_TEAM_DOMAIN = NULL;
$ACCESS_AUD = NULL;

// Login throttling: after this many wrong passphrases from one client address within the lockout time, that client is locked out
// (even with the right passphrase) for $LOGIN_LOCKOUT_SECONDS seconds. IPv6 clients are counted per /64 network. 0 disables throttling.
// Installs whose config.php lacks these lines get the defaults shown here. Defaults = 5 and 900 (15 minutes).
$LOGIN_MAX_ATTEMPTS = 5;
$LOGIN_LOCKOUT_SECONDS = 900;
// -------------------------------

?>
