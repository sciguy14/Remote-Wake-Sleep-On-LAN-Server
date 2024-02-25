REMOTE WAKE/SLEEP-ON-LAN SERVER *(RWSOLS)*
==========================================
The Remote Wake/Sleep-on-LAN Server (RWSOLS) is a simple webapp that runs on your Raspberry Pi to turn it into a remotely accessible Wake/Sleep-On-LAN Server. This is very useful when you have a high-powered machine that you don't want to keep on all the time, but that you want to keep remotely accessible for Remote Desktop, SSH, FTP, etc. Wake-On-LAN packets cannot be forwarded through a router, so to wake up a remote machine behind a router, you need to have something on its local network to wake it up. That's where RWSOLS comes in. RWSOLS can control an unlimited number of remote machines on its local network, and is capable of waking them up (any OS) or putting them to sleep (only Windows remote machines). It can be configured to use signed or unsigned SSL encryption or it can be run over traditional HTTP.
  
A very detailed set of [installation instructions](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki) can be found in the GitHub Wiki. V3 of this software adds an auto-installer script that makes installation very easy, and handles automatic configuration of signed SSL certificate.
  
You'll also find a description of [how it works](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/How-it-Works), [an FAQ](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs), and a list of [relevant terminology](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Terminology) on the Wiki.
  
For more info, see [my blog post about RWSOLS](https://www.jeremyblum.com/2013/07/14/rpi-wol-server/) on my website.
  
If you're having problems with getting RWSOLS working, check the [FAQ](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/wiki/Notes-and-FAQs) first, or [the comments](https://www.jeremyblum.com/2013/07/14/rpi-wol-server/#comments) on my blog. If you still can't get it to work, please [create a GitHub issue](https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server/issues) with specific details.


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
