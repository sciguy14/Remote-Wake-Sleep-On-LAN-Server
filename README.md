REMOTE WAKE/SLEEP-ON-LAN SERVER
=========================
This is a simple webapp that runs on your Raspberry Pi to turn it into a remotely accessible Wake/Sleep-On-LAN Server.  This is very useful when you have high-powered machine that you don't want to keep on all the time, but that you want to keep remotely accessible for Remote Desktop, SSH, FTP, etc.  A detailed guide on how this software works, how to set it up, and more is available at my website. You should follow the instructions ony website first to get your network setup. Then, come back here and follow these instructions to get your Raspberry Pi set up correctly.
  
Follow these instructions first:  
[http://www.jeremyblum.com/2013/07/14/rpi-wol-server/](http://www.jeremyblum.com/2013/07/14/rpi-wol-server/)
  
This guide was updated on January 24th, 2016. It assumes that you are using a freshly installed copy of [Raspian Jessie Lite](https://www.raspberrypi.org/downloads/raspbian/) on any version of the Raspberry Pi.

I can't guarantee that this will work with older versions of Raspian or older versions of Apache (These instructions are tested on Apache 2.4.10).
  
This guide assumes you've already gone through the setup steps listed on [my website](http://www.jeremyblum.com/2013/07/14/rpi-wol-server/), and that you're connected to your Pi over SSH or via the console. It assumes that you're logged in via a normal user (pi), not root.

Setting up your Pi
------------------
### Getting Setup
* First, install the packages we'll need:  
`sudo apt-get install wakeonlan apache2 php5 git php5-curl libapache2-mod-php5`
* The PHP server uses the built-in ping command to check if the remote machine is awake or not. Give all users on the pi permission to ping by executing this command:  
`` sudo chmod u+s `which ping` ``
* Now, clone this repository:  
`git clone https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server.git`
* Apache 2 (as of version 2.4) keeps web files at /var/www/html. Let's take ownership of that directory:  
`sudo chown pi: /var/www/html`

### Enabling Encryption
Enabling TLS/SSL encryption is recommended, but not required. If you don't want encryption, you can skip this section. If you do want to enable encryption, complete these steps, which create the required keys and enable SSL support within Apache 2:
* `sudo mkdir /etc/apache2/ssl`
* `sudo openssl genrsa -out /etc/apache2/ssl/wol.key 2048`
* `sudo openssl req -new -key /etc/apache2/ssl/wol.key -out /etc/apache2/ssl/wol.csr`

At this point you will be asked some questions, most of which have no impact on the running of your service. You can accept the default values, or fill in answers. However, the "Common Name" should be the name of the dynamic DNS you have setup, ("wol.example.com" for example) and the password should be left blank so that the Pi can load it on boot without prompting you for a password.
  
Finish configuring the Apache SSL Support:
* `sudo openssl x509 -req -days 10 -in /etc/apache2/ssl/wol.csr -signkey /etc/apache2/ssl/wol.key -out /etc/apache2/ssl/wol.crt`
* `sudo mv -f Remote-Wake-Sleep-On-LAN-Server/ssl.conf /etc/apache2/mods-available/ssl.conf`
* `sudo a2enmod ssl`

### Setting up the Apache 2 Server and Securing it
If you chose to not enable encryption, resume following the instructions here.
* Enable the headers mod and restart apache:  
`sudo a2enmod headers`  
`sudo service apache2 restart`
* Move the site config file over to the Apache available sites config folder:  
`sudo mv -f Remote-Wake-Sleep-On-LAN-Server/000-default.conf /etc/apache2/sites-available/000-default.conf`
* Make some config adjustments to improve security:  
`sudo sed -i.bak "s/expose_php = On/expose_php = Off/g" /etc/php5/apache2/php.ini`  
`sudo sed -i.bak "s/E_ALL & ~E_NOTICE & ~E_STRICT & ~E_DEPRECATED/error_reporting = E_ERROR/g" /etc/php5/apache2/php.ini`  
`sudo sed -i.bak "s/ServerSignature On/ServerSignature Off/g" /etc/apache2/conf-available/security.conf`  
`sudo sed -i.bak "s/ServerTokens OS/ServerTokens Prod/g" /etc/apache2/conf-available/security.conf`  
* Restart the Apache 2 Service:  
`sudo service apache2 restart`
* Move the Website files over to the serving directory:  
`mv Remote-Wake-Sleep-On-LAN-Server/* /var/www/html`  
`mv Remote-Wake-Sleep-On-LAN-Server/.htaccess /var/www/html`  
`rm -rf Remote-Wake-Sleep-On-LAN-Server/`  
`rm -f /var/www/html/index.html`  
`mv /var/www/html/config_sample.php /var/www/html/config.php`  

### Finishing the Configuration
Now, the Apache 2 server is setup and secured. It's serving up your site. But, before it works, you need to adjust the configuration values to match your network setup. Open the config file and adjust the values to match your network setup:  
`nano /var/www/html/config.php`  

You'll need to enter a value for `$APPROVED_HASH` and optionally **turn on SSL enforcing (do this if you followed the above steps to enable encryption by setting `$USE_HTTPS` to `true`)**. To generate an approved hash, think of the password you want to use, and use the website linked from the config file to generate the one-way hash. Then paste that in to the config file. Don't forget to also set the other parameters in the config file, including the IP and MAC address of the computer you want to control.

Additional Notes
----------------
You will also likely want to port forward from your router to the Pi so that this service is accessible externally.  If you are using TLS/SSL you most likely want your port forward to point to TCP/443 on the Pi, if unencrypted you probably want TCP/80. More details about how to do that are included in the tutorial on my blog.

And finally, a short warning that self-signed certificates are not perfect.  They will likely cause a warning message in your browser.  This is because the certificate is not linked to a trusted certificate authority (CA).  The implications of this are that a Man-in-the-Middle attacker could theoretically insert a certificate that they control into your communication with the Pi and as a result would be able to read the encrypted messages being transferred.  In summary, encryption using a self-signed certificate is not perfect, but better than none at all.

License
-------
This work is licensed under the [GNU GPL v3](http://www.gnu.org/licenses/gpl.html).
Please share improvements or remixes with the community, and attribute me (Jeremy Blum, <http://www.jeremyblum.com>) when reusing portions of my code.

Other contributors to this work include:
- Felix Ryan (https://www.felixrr.pro)
