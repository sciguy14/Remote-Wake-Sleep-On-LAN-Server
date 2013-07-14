REMOTE WAKE-ON-LAN SERVER
=========================
This is a fairly simple web-app that is designed to be run on a low power server (I used a Raspberry Pi). I have a very power hungry desktop computer that I use for video editing and other large file storage. When I'm travelling with my laptop, I obviously can't carry all my files and data with me, so I ocassionally use "remote desktop" to access my desktop from the road. However, the computer has to be on for me to be able to do this. This means I need to leave the computer on all the time, just in case I need to access it. This wastes power, costs me money, and harms the environment. My Raspberry Pi, on the other hand, consumes a miniscule amount of power, and is always on anyways, serving other purposes. Follow the steps below to run a simple mobile-friendly website from your Raspberry Pi that will allow you to remotely wake up your power-hungry computer, so you can remotely access it. When your done, just put it back to sleep from within the remote desktop service. The app pings the computer from the Raspberry Pi to inform you of when the computer has woken up and established network connectivity.



License
-------
This work is licensed under the GNU GPL v3.
Please share improvements or remixes with the community, and attribute me (Jeremy Blum, <http://www.jeremyblum.com>) when reusing portions of my code.
http://www.gnu.org/licenses/gpl.html
