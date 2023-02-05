<?php /*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
Original Author: Jeremy E. Blum (https://www.jeremyblum.com)
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
    	<?php
    		$approved = false;
			$wake_up = false;
			$go_to_sleep = false;
			$check_current_status = false;

			if ( isset($_POST['password']) )
            {
                if (!is_null($APPROVED_HASH))
                {
                    if (password_verify($_POST['password'], $APPROVED_HASH))
	                {
						if ($_POST['submitbutton'] == "Wake Up!")
						{
							$approved = true;
							$wake_up = true;
						}
						elseif ($_POST['submitbutton'] == "Sleep!")
						{
							$approved = true;
							$go_to_sleep = true;
						}
						elseif ($_POST['submitbutton'] == "Check Status")
						{
							$approved = true;
							$check_current_status = true;
						}
					}
                }
			}

			$selectedComputer = $_GET['computer'];

			# Add $DEBUG = true; to config file to include this debugging output.
    		if ( isset($DEBUG) && $DEBUG == true )
    		{
	    		echo "<pre>";
	    		echo print_r($_POST, true);
	    		echo "Approved: ";
	    		echo $approved ? 'true' : 'false';
	    		echo "</pre>";
    		}
    	?>
    	<form class="form-signin" method="post">
        	<h3 class="form-signin-heading">
			<?php
				

			 	echo "Remote Wake/Sleep-On-LAN</h3>";
				if ($wake_up) {
					echo "Waking Up!";
				} elseif ($go_to_sleep) {
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
                $show_form = true;

                if ($check_current_status)
                {
                	echo "<p>Approved. Please wait while the computer is queried for its current status...</p>";
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
                elseif ($wake_up)
                {
                	echo "<p>Approved. Sending WOL Command...</p>";
					exec ('wakeonlan ' . $COMPUTER_MAC[$selectedComputer]);
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
						$asleep = true;
					}
				}
				elseif ($go_to_sleep)
				{
					echo "<p>Approved. Sending Sleep Command...</p>";
					$ch = curl_init();
					curl_setopt($ch, CURLOPT_URL, "http://" . $COMPUTER_LOCAL_IP[$selectedComputer] . ":" . $COMPUTER_SLEEP_CMD_PORT . "/" .  $COMPUTER_SLEEP_CMD);
					curl_setopt($ch, CURLOPT_TIMEOUT, 5);
					curl_setopt($ch, CURLOPT_RETURNTRANSFER, TRUE);
					
					if (curl_exec($ch) === false)
					{
						echo "<p><span style='color:#CC0000;'><b>Command Failed:</b></span> " . curl_error($ch) . "</p>";
						echo "<p style='color:#CC0000;'>" . $COMPUTER_NAME[$selectedComputer] . " doesn't seem to be falling asleep... Try again?</p><p>(Or <a href='?computer=" . $selectedComputer . "'>Return to the Wake/Sleep Control Home</a>.)</p>";
							$asleep = false;
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
							$asleep = false;
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
        			<input type="password" autocomplete=off class="input-block-level" placeholder="Enter Passphrase" <?php if (isset($approved) && $approved == true) {echo "value='" . $_POST['password'] . "'";} ?> name="password">
        			<?php if ( !isset($_POST['submitbutton']) || ($approved == false) ) { ?>
        			    <input class="btn btn-large btn-primary" type="submit" name="submitbutton" value="Check Status"/>
						<input type="hidden" name="submitbutton" value="Check Status" />  <!-- handle if IE used and enter button pressed instead of sleep button -->
                    <?php } elseif (  $asleep ) { ?>
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
    </div> <!-- /container -->
    <script src="<?php echo $BOOTSTRAP_LOCATION_PREFIX; ?>bootstrap/js/bootstrap.min.js"></script>
  </body>
</html>
