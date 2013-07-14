<?PHP /*
Remote Wake-On-LAN Server
https://github.com/sciguy14/Remote-Wake-On-LAN-Server
Author: Jeremy E. Blum (http://www.jeremyblum.com)
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)
*/ ?>

<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Remote Wake-On-LAN</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="">
    <meta name="author" content="">

    <!-- Le styles -->
    <link href="bootstrap/css/bootstrap.css" rel="stylesheet">
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
    <link href="bootstrap/css/bootstrap-responsive.css" rel="stylesheet">

    <!-- HTML5 shim, for IE6-8 support of HTML5 elements -->
    <!--[if lt IE 9]>
      <script src="bootstrap/js/html5shiv.js"></script>
    <![endif]-->

    <!-- Fav and touch icons -->
    <link rel="apple-touch-icon-precomposed" sizes="144x144" href="bootstrap/ico/apple-touch-icon-144-precomposed.png">
    <link rel="apple-touch-icon-precomposed" sizes="114x114" href="bootstrap/ico/apple-touch-icon-114-precomposed.png">
      <link rel="apple-touch-icon-precomposed" sizes="72x72" href="bootstrap/ico/apple-touch-icon-72-precomposed.png">
                    <link rel="apple-touch-icon-precomposed" href="bootstrap/ico/apple-touch-icon-57-precomposed.png">
                                   <link rel="shortcut icon" href="bootstrap/ico/favicon.png">
  </head>

  <body>

    <div class="container">
    	<form class="form-signin" action="/" method="post">
        	<h2 class="form-signin-heading">Remote Wake-On-LAN</h2>
			<?php 
			
				//You should not need to edit this file. Adjust Paramets in the config file:
				require_once('config.php');
			
                // Enable flushing
                ini_set('implicit_flush', true);
                ob_implicit_flush(true);
                ob_end_flush();
                
                $show_form = true;
                
                if ( isset($_POST['password']) )
                {
                    $hash = hash("sha256", $_POST['password']);
                    if ($hash == $APPROVED_HASH)
                    {
                        echo "<p>Approved. Sending WOL Command...</p>";
                        exec ('wakeonlan ' . $COMPUTER_MAC);
                        echo "<p>Command Sent. Waiting for computer to wake up...</p><p>";
                        $count = 1;
                        $down = true;
                        while ($count <= $MAX_PINGS && $down == true)
                        {
                            echo "Ping " . $count . "...";
                            $pinginfo = exec("ping -c 1 " . $COMPUTER_LOCAL_IP);
                            $count++;
                            if ($pinginfo != "")
                            {
                                $down = false;
                                echo "<span style='color:#00CC00;'><b>It's Alive!</b></span><br />";
                                $show_form = false;
                            }
                            else
                            {
                                echo "<span style='color:#CC0000;'><b>Still Down.</b></span><br />";
                            }	
                        }
						echo "</p>";
                        if ($down == true)
                        {
                            echo "<p style='color:#CC0000;'><b>FAILED!</b> The remote computer doesn't seem to be waking up... Try again?</p>";
                        }
                        
                    }
                    else
                    {
                        echo "<p style='color:#CC0000;'><b>DENIED.</b></p>";
                    }		
                }
                
                if ($show_form)
                {
            ?>
        			<input type="password" class="input-block-level" placeholder="Enter Passphrase" name="password">
        			<button class="btn btn-large btn-primary" type="submit">Wake Up!</button>
	
			<?php
				}
			?>
		</form>            
    </div> <!-- /container -->
    <script src="bootstrap/js/bootstrap.min.js"></script>
  </body>
</html>