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

// The rest of the value should be set by you based on your configuration of computers to wake/sleep
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

?>
