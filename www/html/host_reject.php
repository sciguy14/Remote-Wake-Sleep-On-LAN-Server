<?php
/*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)

Apache routes requests with a disallowed Host header here (any path). Always answers 421 with
an empty body.
*/

require_once(__DIR__ . '/includes/host_guard.php');

function rwsols_host_reject_logging_enabled()
{
	$LOG_HOST_REJECTIONS = false;
	$config = __DIR__ . '/config.php';
	if (@is_file($config)) {
		ob_start();
		try {
			include($config);
		} catch (\Throwable $e) {
		}
		ob_end_clean();
	}
	return !empty($LOG_HOST_REJECTIONS);
}

if (rwsols_host_reject_logging_enabled()) {
	rwsols_log_host_rejection(
		isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : null,
		isset($_SERVER['HTTP_HOST']) ? $_SERVER['HTTP_HOST'] : null
	);
}

while (ob_get_level() > 0) {
	@ob_end_clean();
}
@ini_set('default_mimetype', '');
if (!headers_sent()) {
	header_remove('X-Powered-By');
	http_response_code(421);
	header('Content-Length: 0');
}
exit;
