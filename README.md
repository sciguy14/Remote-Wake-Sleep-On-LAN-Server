REMOTE WAKE/SLEEP-ON-LAN SERVER
=========================
This is a simple webapp that runs on your Raspberry Pi to turn it into a remotely accessible Wake/Sleep-On-LAN Server.  This is very useful when you have high-powered machine that you don't want to keep on all the time, but that you want to keep remotely accessible for Remote Desktop, SSH, FTP, etc.  The original author made a very detailed guide which can be found at his website:
[http://www.jeremyblum.com/2013/07/14/rpi-wol-server/](http://www.jeremyblum.com/2013/07/14/rpi-wol-server/)

How-to Guide
------------
For tech-happy and not-so-tech-happy people this is the low down:
- sudo apt-get install wakeonlan apache2 php5 git php5-curl
- git clone https://github.com/felixrr/Remote-Wake-Sleep-On-LAN-Server.git
- sudo chown pi: /var/www

You may not want TLS/SSL encryption (though it is recommended), if so skip the next few lines

- sudo mkdir /etc/apache2/ssl
- sudo openssl genrsa -out /etc/apache2/ssl/wol.key 2048
- sudo openssl req -new -key /etc/apache2/ssl/wol.key -out /etc/apache2/ssl/wol.csr

At this point you will be asked some questions, most of which have no impact on the running of your service.  However, the "Common Name" should be the name of any DNS you have setup, so for example "wol.home.com" and the password should be left blank in order for the Pi to be able to load it on boot without you entering a password.

- sudo openssl x509 -req -days 10 -in /etc/apache2/ssl/wol.csr -signkey /etc/apache2/ssl/wol.key -out /etc/apache2/ssl/wol.crt
- sudo mv -f Remote-Wake-On-LAN-Server/apachesslconfig /etc/apache2/mods-available/ssl.conf
- sudo a2enmod ssl

If you skipped TLS/SSL start again here, if you didn't skip you still need the following bits:

- sudo a2enmod headers
- sudo service apache2 restart
- sudo mv -f Remote-Wake-On-LAN-Server/apacheconfig /etc/apache2/sites-enabled/000-default
- sudo sed -i.bak "s/expose_php = On/expose_php = Off/g" /etc/php5/apache2/php.ini
- sudo sed -i.bak "s/E_ALL & ~E_NOTICE & ~E_STRICT & ~E_DEPRECATED/error_reporting = E_ERROR/g" /etc/php5/apache2/php.ini
- sudo sed -i.bak "s/ServerSignature On/ServerSignature Off/g" /etc/apache2/conf.d/security
- sudo sed -i.bak "s/ServerTokens Full/ServerTokens Prod/g" /etc/apache2/conf.d/security
- mv Remote-Wake-On-LAN-Server/* /var/www
- mv Remote-Wake-On-LAN-Server/.htaccess /var/www
- rm -rf Remote-Wake-On-LAN-Server/
- rm -f /var/www/index.html
- mv /var/www/config_sample.php /var/www/config.php
- nano /var/www/config.php

At this point you will need to edit the config.php file to give it a value for "$APPROVED_HASH" and optionally turn on SSL enforcing (recommended).

You will also likely want to port forward from your router to the Pi so that this service is accessible externally.  If you are using TLS/SSL you most likely want your port forward to point to TCP/443 on the Pi, if unencrypted you probably want TCP/80.

And finally, a short warning that self-signed certificates are not perfect.  They will likely cause a warning message in your browser.  This is because the certificate is not linked to a trusted certificate authority (CA).  The implications of this are that a Man-in-the-Middle attacker could theoretically insert a certificate that they control into your communication with the Pi and as a result would be able to read the encrypted messages being transferred.  In summary, encryption using a self-signed certificate is not perfect, but better than none at all.

License
-------
This work is licensed under the [GNU GPL v3](http://www.gnu.org/licenses/gpl.html).
Please share improvements or remixes with the community, and attribute me (Jeremy Blum, <http://www.jeremyblum.com>) when reusing portions of my code.

Other contributors to this work include:
- Felix Ryan (https://www.felixrr.pro)
