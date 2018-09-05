<?php /*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
Original Author: Jeremy E. Blum (http://www.jeremyblum.com)
Security Edits By: Felix Ryan (https://www.felixrr.pro)
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)
*/ 

//You should not need to edit this file. Adjust Parameters in the config file:
require_once('config.php');

//set headers that harden the HTTPS session
if ($USE_HTTPS)
{
   header("Strict-Transport-Security: max-age=7776000"); //HSTS headers set for 90 days
}

// Enable flushing
ini_set('implicit_flush', true);
ob_implicit_flush(true);
ob_end_flush();

//Set the correct protocol
if ($USE_HTTPS && !$_SERVER['HTTPS'])
{
   header("Location: https://$_SERVER[HTTP_HOST]$_SERVER[REQUEST_URI]");
   exit;
}

//function to show warnings
function warning($msg) { 
	$script = "<script type='text/javascript'>$('#warning').fadeIn(1000);</script>";
	$script .= "<script type='text/javascript'>$('#warning').html('<strong>Warning!</strong> $msg');</script>";
	return $script;
}

//check if wol application (either wol or awake) is installed and set wol application to be used (e.g. for use with alpine & awake instead of wol)
$wol_apps = [ "wakeonlan", "awake" ];//could be extended
foreach ($wol_apps as $app) {
  if (exec("command -v ".$app)<>"")
  {
    $wol = $app;//set the first application from the array that returns a non empty string as the wol application to be used
    break;
  }
  if ($app === end($wol_apps))
  {
    $apps = implode(', ', $wol_apps);
    $script = warning("unable to find any wol app, been looking for: <strong>".$apps."</strong> (one is necessary).<br>install one e.g. by running <strong>apt-get install wakeonlan</strong> or <strong>apk add awake</strong> (on the server)");//if non of the apps from the array are installed show a warning
  }
}

//check if ping application is installed & working
$ping_apps = [ "ping" ];//could be extended
foreach ($ping_apps as $app) {
  if (exec("command -v ".$app)<>"")
  {
    break;//just check it exists
  }
  if ($app === end($ping_apps))
  {
	$apps = implode(', ', $ping_apps);
    $script = warning("Unable to find any ping app, been looking for: <strong>".$apps."</strong> (one is necessary).<br>Install one e.g. by running <strong>apt-get install iputils-ping</strong> or <strong>apk add iputils</strong> (on the server)");//if non of the apps from the array are installed show a warning
  }
}
if (exec("ping -c 1 127.0.0.1 > /dev/null && echo $?")=="")
{
  $script = warning("The ping command is not working correctly. I tested this by pinging 127.0.0.1. This could be caused by a rights problem (are you root). An easy fix is usually to install iputils (apt-get install iputils-ping, apk add iputils");//if non of the apps from the array are installed show a warning
}

//Set default computer (this is business logic so should be done last)
if (empty($_GET))
{
   header('Location: '. ($USE_HTTPS ? "https://" : "http://") . "$_SERVER[HTTP_HOST]$_SERVER[REQUEST_URI]" . "?computer=0");
   exit;
}
else
   $_GET['computer'] = preg_replace("/[^0-9,.]/", "", $_GET['computer']);

?>

<!DOCTYPE html>
<html lang="en" >
  <head>
    <title>Remote Wake/Sleep-On-LAN</title>
    <meta http-equiv="Content-Type" content="text/html;charset=utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="A utility for remotely waking/sleeping a Windows computer via a Raspberry Pi">
    <meta name="author" content="Jeremy Blum">

	<!-- jQuery -->
	<script src="include/jquery-3.3.1.min.js"></script>
	
    <!-- Le styles -->
    <link href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/css/bootstrap.css" rel="stylesheet">
    <style type="text/css">
      body {
        padding-top: 40px !important;
        padding-bottom: 40px;
        background-color: #f5f5f5;
      }

      .form-signin {
        max-width: 600px;
        padding: 19px 29px 29px;
        margin: 0 auto 20px;
        background-color: #fff;
        border: 1px solid #e5e5e5;
        -webkit-border-radius: 5px;
           -moz-border-radius: 5px;
                border-radius: 5px;
        -webkit-box-shadow: 0 1px 2px rgba(0,0,0,.05);
           -moz-box-shadow: 0 1px 2px rgba(0,0,0,.05);
                box-shadow: 0 1px 2px rgba(0,0,0,.05);
      }
      .form-signin .form-signin-heading,
      .form-signin .checkbox {
        margin-bottom: 10px;
      }
      .form-signin input[type="text"],
      .form-signin input[type="password"] {
        font-size: 16px;
        height: auto;
        margin-bottom: 15px;
        padding: 7px 9px;
      }

    </style>
    <link href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/css/bootstrap-responsive.css" rel="stylesheet">

    <!-- HTML5 shim, for IE6-8 support of HTML5 elements -->
    <!--[if lt IE 9]>
      <script src="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/js/html5shiv.js"></script>
    <![endif]-->

    <!-- Fav and touch icons -->
    <link rel="apple-touch-icon-precomposed" sizes="144x144" href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/ico/apple-touch-icon-144-precomposed.png">
    <link rel="apple-touch-icon-precomposed" sizes="114x114" href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/ico/apple-touch-icon-114-precomposed.png">
    <link rel="apple-touch-icon-precomposed" sizes="72x72" href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/ico/apple-touch-icon-72-precomposed.png">
    <link rel="apple-touch-icon-precomposed" href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/ico/apple-touch-icon-57-precomposed.png">
    <link rel="shortcut icon" href="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/ico/favicon.png">
  </head>

  <body>

    <div class="container">
    	<form class="form-signin" method="post">
        	<h3 class="form-signin-heading">
			<?php
				//print_r($_POST); //Useful for POST Debugging
				$approved_wake = false;
				$approved_sleep = false;
				if ( isset($_POST['password']) )
		                {
                			$hash = hash("sha256", $_POST['password']);
			                if ($hash == $APPROVED_HASH)
			                {
						if ($_POST['submitbutton'] == "Wake Up!")
						{
							$approved_wake = true;
						}
						elseif ($_POST['submitbutton'] == "Sleep!")
						{
							$approved_sleep = true;
						}
					}
				}

				$selectedComputer = $_GET['computer'];

			 	echo "Remote Wake/Sleep-On-LAN</h3>";
				if ($approved_wake) {
					echo "Waking Up!";
				} elseif ($approved_sleep) {
					echo "Going to Sleep!";
				} else {?>
                    <select name="computer" onchange="if (this.value) window.location.href='?computer=' + this.value">
                    <?php
                        for ($i = 0; $i < count($COMPUTER_NAME); $i++)
                        {
                            echo "<option value='" . $i;
                            if( $selectedComputer == $i)
							{
								echo "' selected>";
							}
                            else
							{
								echo "'>";
							}
							echo $COMPUTER_NAME[$i] . "</option>";
                
                        }
                    ?>
                    </select>

				<?php } ?>
            <?php

				if (!isset($_POST['submitbutton']) || (isset($_POST['submitbutton']) && !$approved_wake && !$approved_sleep))
				{
					echo "<h5 id='wait'>Querying Computer State. Please Wait...</h5>";
					$pinginfo = exec("ping -c 1 " . $COMPUTER_LOCAL_IP[$selectedComputer]);
	    				?>
	    				<script>
						document.getElementById('wait').style.display = 'none';
				        </script>
	   					<?php
					if ($pinginfo == "")
					{
						$asleep = true;
						echo "<h5>" . $COMPUTER_NAME[$selectedComputer] . " is presently asleep.</h5>";
					}
					else
					{
						$asleep = false;
						echo "<h5>" . $COMPUTER_NAME[$selectedComputer] . " is presently awake.</h5>";
					}
				}

                $show_form = true;

                if ($approved_wake)
                {
                	echo "<p>Approved. Sending WOL Command...</p>";
					if ($wol == "wakeonlan") {																  
						exec ('wakeonlan ' . $COMPUTER_MAC[$selectedComputer]);
					} elseif ($wol == "awake") {
						exec ('awake -d ' . $COMPUTER_LOCAL_IP[$selectedComputer] . " " . $COMPUTER_MAC[$selectedComputer]);//for awake the broadcast didn't seem to work
					} else {
						warning("Not possible! No wol application installed on server.");//if non of the apps from the array are installed show a warning
					}
					echo "<p>Command Sent. Waiting for " . $COMPUTER_NAME[$selectedComputer] . " to wake up...</p><p>";
					$count = 1;
					$down = true;
					while ($count <= $MAX_PINGS && $down == true)
					{
						echo "Ping " . $count . "...";
						$pinginfo = exec("ping -c 1 " . $COMPUTER_LOCAL_IP[$selectedComputer]);
						$count++;
						if ($pinginfo != "")
						{
							$down = false;
							echo "<span style='color:#00CC00;'><b>It's Alive!</b></span><br />";
							echo "<p><a href='?computer=" . $selectedComputer . "'>Return to the Wake/Sleep Control Home</a></p>";
							$show_form = false;
						}
						else
						{
							echo "<span style='color:#CC0000;'><b>Still Down.</b></span><br />";
						}
						sleep($SLEEP_TIME);
					}
					echo "</p>";
					if ($down == true)
					{
						echo "<p style='color:#CC0000;'><b>FAILED!</b> " . $COMPUTER_NAME[$selectedComputer] . " doesn't seem to be waking up... Try again?</p><p>(Or <a href='?computer=" . $selectedComputer . "'>Return to the Wake/Sleep Control Home</a>.)</p>";
					}
				}
				elseif ($approved_sleep)
				{
					echo "<p>Approved. Sending Sleep Command...</p>";
					$ch = curl_init();
					curl_setopt($ch, CURLOPT_URL, "http://" . $COMPUTER_LOCAL_IP[$selectedComputer] . ":" . $COMPUTER_SLEEP_CMD_PORT . "/" .  $COMPUTER_SLEEP_CMD);
					curl_setopt($ch, CURLOPT_TIMEOUT, 5);
					curl_setopt($ch, CURLOPT_RETURNTRANSFER, TRUE);
					
					if (curl_exec($ch) === false)
					{
						echo "<p><span style='color:#CC0000;'><b>Command Failed:</b></span> " . curl_error($ch) . "</p>";
					}
					else
					{
						echo "<p><span style='color:#00CC00;'><b>Command Succeeded!</b></span> Waiting for " . $COMPUTER_NAME[$selectedComputer] . " to go to sleep...</p><p>";
						$count = 1;
						$down = false;
						while ($count <= $MAX_PINGS && $down == false)
						{
							echo "Ping " . $count . "...";
							$pinginfo = exec("ping -c 1 " . $COMPUTER_LOCAL_IP[$selectedComputer]);
							$count++;
							if ($pinginfo == "")
							{
								$down = true;
								echo "<span style='color:#00CC00;'><b>It's Asleep!</b></span><br />";
								echo "<p><a href='?computer=" . $selectedComputer . "'>Return to the Wake/Sleep Control Home</a></p>";
								$show_form = false;
								
							}
							else
							{
								echo "<span style='color:#CC0000;'><b>Still Awake.</b></span><br />";
							}
							sleep($SLEEP_TIME);
						}
						echo "</p>";
						if ($down == false)
						{
							echo "<p style='color:#CC0000;'><b>FAILED!</b> " . $COMPUTER_NAME[$selectedComputer] . " doesn't seem to be falling asleep... Try again?</p><p>(Or <a href='?computer=" . $selectedComputer . "'>Return to the Wake/Sleep Control Home</a>.)</p>";
						}
					}
					curl_close($ch);
				}
				elseif (isset($_POST['submitbutton']))
				{
					echo "<p style='color:#CC0000;'><b>Invalid Passphrase. Request Denied.</b></p>";
				}		
                
                if ($show_form)
                {
            ?>
        			<input type="password" autocomplete=off class="input-block-level" placeholder="Enter Passphrase" name="password">
                    <?php if ( (isset($_POST['submitbutton']) && $_POST['submitbutton'] == "Wake Up!") || (!isset($_POST['submitbutton']) && $asleep) ) {?>
        				<input class="btn btn-large btn-primary" type="submit" name="submitbutton" value="Wake Up!"/>
						<input type="hidden" name="submitbutton" value="Wake Up!"/>  <!-- handle if IE used and enter button pressed instead of wake up button -->
                    <?php } else { ?>
		                <input class="btn btn-large btn-primary" type="submit" name="submitbutton" value="Sleep!"/>
						<input type="hidden" name="submitbutton" value="Sleep!" />  <!-- handle if IE used and enter button pressed instead of sleep button -->
                    <?php } ?>	
	
			<?php
				}
			?>
		</form>
		<div class="alert alert-warning" id="warning" style="display: none;">
		</div>																	   
    </div> <!-- /container -->
    <script src="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/js/bootstrap.min.js"></script>
<?php echo $script;?>					 
  </body>
</html>
