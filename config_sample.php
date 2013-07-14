<?php
	/*
	Remote Wake-On-LAN Server [CONFIGURATION FILE]
	https://github.com/sciguy14/Remote-Wake-On-LAN-Server
	Author: Jeremy E. Blum (http://www.jeremyblum.com)
	License: GPL v3 (http://www.gnu.org/licenses/gpl.html)
	
	UPDATE THE VALUES IN THIS FILE AND CHANGE THE NAME TO: "config.php"
	*/

	//Choose a passphrase and find the sha256 hash of that passphrase.
	//You can use an online calculator to generate the hash: http://www.xorbin.com/tools/sha256-hash-calculator.
	//Unless you are using an SSL connection to your server, remember that passphrases could still be obtained via a man-in-the-middle attack.
	$APPROVED_HASH = "PUT SHA256 HASH OF YOUR PASSPHRASE HERE";
	
	//This is the number of times that the WOL server will try to ping the target computer to check if it has woken up. Default = 10.
	$MAX_PINGS = 10;
	
	//This is the MAC address of the Network Interface on the computer you are trying to wake.
	$COMPUTER_MAC = '00:00:00:00:00:00';
	
	//This is the LOCAL IP address of the computer you are trying to wake.  Use a reserved DHCP through your router's administration interace to ensure it doesn't change.
	$COMPUTER_LOCAL_IP = '192.168.1.100';
?>