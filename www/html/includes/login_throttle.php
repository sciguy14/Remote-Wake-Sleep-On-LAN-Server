<?php
/*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)

Per-client login throttling: after too many wrong passphrases a client is locked out for a
fixed time. State is one small file per client in a private temp directory.
This file only defines functions; index.php calls them.
*/

require_once(__DIR__ . '/host_guard.php');

if (!defined('RWSOLS_LOGIN_DEFAULT_MAX')) {
	define('RWSOLS_LOGIN_DEFAULT_MAX', 5);
	define('RWSOLS_LOGIN_DEFAULT_LOCKOUT', 900);
	define('RWSOLS_LOGIN_FILE_PREFIX', 'login-');
	define('RWSOLS_LOGIN_CLEANUP_ONE_IN', 50);
}

// Reads $LOGIN_MAX_ATTEMPTS and $LOGIN_LOCKOUT_SECONDS from $vars (normally $GLOBALS).
// Unset or non-numeric values give the defaults. max <= 0 disables throttling.
// The failure-counting window equals the lockout length.
function rwsols_login_config($vars = null)
{
	if (!is_array($vars)) {
		$vars = $GLOBALS;
	}
	$max = RWSOLS_LOGIN_DEFAULT_MAX;
	if (isset($vars['LOGIN_MAX_ATTEMPTS']) && is_numeric($vars['LOGIN_MAX_ATTEMPTS'])) {
		$max = (int) $vars['LOGIN_MAX_ATTEMPTS'];
	}
	$lockout = RWSOLS_LOGIN_DEFAULT_LOCKOUT;
	if (isset($vars['LOGIN_LOCKOUT_SECONDS']) && is_numeric($vars['LOGIN_LOCKOUT_SECONDS']) && (int) $vars['LOGIN_LOCKOUT_SECONDS'] > 0) {
		$lockout = (int) $vars['LOGIN_LOCKOUT_SECONDS'];
	}
	return array('max' => $max, 'window' => $lockout, 'lockout' => $lockout);
}

function rwsols_login_normalize_config(array $cfg)
{
	$defaults = rwsols_login_config(array());
	$cfg = array_merge($defaults, $cfg);
	$cfg['max'] = (int) $cfg['max'];
	$cfg['window'] = max(1, (int) $cfg['window']);
	$cfg['lockout'] = max(1, (int) $cfg['lockout']);
	return $cfg;
}

// Reduces an IP address to the throttling key: IPv4 as is (IPv4-mapped IPv6 becomes IPv4),
// other IPv6 to its /64 prefix like "2001:db8:0:1::/64". Anything invalid gives 'unknown'.
function rwsols_login_normalize_ip($ip)
{
	if (!is_string($ip)) {
		return 'unknown';
	}
	$ip = trim($ip);
	if ($ip === '' || filter_var($ip, FILTER_VALIDATE_IP) === false) {
		return 'unknown';
	}
	$bin = @inet_pton($ip);
	if ($bin === false) {
		return 'unknown';
	}
	if (strlen($bin) === 4) {
		return (string) @inet_ntop($bin);
	}
	if (strlen($bin) !== 16) {
		return 'unknown';
	}
	if (substr($bin, 0, 12) === str_repeat("\0", 10) . "\xff\xff") {
		return (string) @inet_ntop(substr($bin, 12));
	}
	$prefix = @inet_ntop(substr($bin, 0, 8) . str_repeat("\0", 8));
	if (!is_string($prefix)) {
		return 'unknown';
	}
	return $prefix . '/64';
}

// $server is normally $_SERVER. On the loopback tunnel vhost the client address comes from the
// CF-Connecting-IP header (when valid); everywhere else the header is forgeable, so REMOTE_ADDR.
function rwsols_login_client_key(array $server, $is_tunnel_origin)
{
	if ($is_tunnel_origin === true && isset($server['HTTP_CF_CONNECTING_IP']) && is_string($server['HTTP_CF_CONNECTING_IP'])) {
		$candidate = trim($server['HTTP_CF_CONNECTING_IP']);
		if (filter_var($candidate, FILTER_VALIDATE_IP) !== false) {
			return rwsols_login_normalize_ip($candidate);
		}
	}
	return rwsols_login_normalize_ip(isset($server['REMOTE_ADDR']) ? $server['REMOTE_ADDR'] : null);
}

// Directory for the counter files or NULL (after logging) if none is usable.
function rwsols_login_dir(array $opts)
{
	$dir = isset($opts['dir']) ? $opts['dir'] : rwsols_private_tmp_dir();
	if (!is_string($dir) || !rwsols_dir_is_private($dir)) {
		rwsols_rate_limited_log('login-throttle', 'login throttle disabled: counter directory unavailable', $opts);
		return null;
	}
	return rtrim($dir, '/\\');
}

function rwsols_login_file($dir, $key)
{
	return $dir . DIRECTORY_SEPARATOR . RWSOLS_LOGIN_FILE_PREFIX . substr(hash('sha256', (string) $key), 0, 40) . '.json';
}

// Normalizes decoded state to array('count' => int, 'first' => int, 'locked_until' => int).
function rwsols_login_state($decoded)
{
	$state = array('count' => 0, 'first' => 0, 'locked_until' => 0);
	if (is_array($decoded)) {
		foreach ($state as $k => $v) {
			if (isset($decoded[$k]) && is_int($decoded[$k]) && $decoded[$k] >= 0) {
				$state[$k] = $decoded[$k];
			}
		}
	}
	return $state;
}

function rwsols_login_state_is_expired(array $state, array $cfg, $now)
{
	if ($state['locked_until'] > $now) {
		return false;
	}
	return $state['count'] === 0 || $now - $state['first'] >= $cfg['window'] || $now < $state['first'];
}

function rwsols_login_status(array $state, $now)
{
	if ($state['locked_until'] > $now) {
		return array('locked' => true, 'retry_after' => $state['locked_until'] - $now);
	}
	return array('locked' => false, 'retry_after' => 0);
}

// Call BEFORE verifying the password. While 'locked' is true the password must not be checked.
// Options: dir (string), clock (callable), logger (callable), state_file (rate-limited log file).
function rwsols_login_check($key, array $cfg, array $opts = array())
{
	$open = array('locked' => false, 'retry_after' => 0);
	try {
		$cfg = rwsols_login_normalize_config($cfg);
		if ($cfg['max'] <= 0) {
			return $open;
		}
		$dir = rwsols_login_dir($opts);
		if ($dir === null) {
			return $open;
		}
		$file = rwsols_login_file($dir, $key);
		if (@is_link($file) || !@is_file($file)) {
			return $open;
		}
		$fh = @fopen($file, 'r');
		if ($fh === false) {
			return $open;
		}
		@flock($fh, LOCK_SH);
		$raw = @stream_get_contents($fh);
		@flock($fh, LOCK_UN);
		@fclose($fh);
		$state = rwsols_login_state(is_string($raw) ? json_decode($raw, true) : null);
		return rwsols_login_status($state, rwsols_clock($opts));
	} catch (\Throwable $e) {
		return $open;
	}
}

// Records one wrong password. Returns the same shape as rwsols_login_check(); 'locked' is true
// when this failure started the lockout. Failures while already locked change nothing, so the
// lockout ends at a fixed time.
function rwsols_login_record_failure($key, array $cfg, array $opts = array())
{
	$open = array('locked' => false, 'retry_after' => 0);
	try {
		$cfg = rwsols_login_normalize_config($cfg);
		if ($cfg['max'] <= 0) {
			return $open;
		}
		$dir = rwsols_login_dir($opts);
		if ($dir === null) {
			return $open;
		}
		$now = rwsols_clock($opts);
		$file = rwsols_login_file($dir, $key);
		if (@is_link($file)) {
			return $open;
		}
		$fh = @fopen($file, 'c+');
		if ($fh === false) {
			rwsols_rate_limited_log('login-throttle', 'login throttle disabled: cannot write counter file', $opts);
			return $open;
		}
		@flock($fh, LOCK_EX);
		$raw = @stream_get_contents($fh);
		$state = rwsols_login_state(is_string($raw) ? json_decode($raw, true) : null);

		$started_lockout = false;
		if ($state['locked_until'] > $now) {
			// Already locked: leave the state alone.
			$result = rwsols_login_status($state, $now);
		} else {
			if (rwsols_login_state_is_expired($state, $cfg, $now)) {
				$state = array('count' => 0, 'first' => 0, 'locked_until' => 0);
			}
			if ($state['count'] === 0) {
				$state['first'] = $now;
			}
			$state['count']++;
			if ($state['count'] >= $cfg['max']) {
				$state = array('count' => 0, 'first' => 0, 'locked_until' => $now + $cfg['lockout']);
				$started_lockout = true;
			}
			@ftruncate($fh, 0);
			@rewind($fh);
			@fwrite($fh, json_encode($state));
			@fflush($fh);
			$result = rwsols_login_status($state, $now);
		}
		@flock($fh, LOCK_UN);
		@fclose($fh);

		if ($started_lockout) {
			$logger = (isset($opts['logger']) && is_callable($opts['logger'])) ? $opts['logger'] : 'rwsols_syslog';
			call_user_func($logger, 'login lockout client=' . rwsols_sanitize_log_value((string) $key, 64) . ' seconds=' . $cfg['lockout']);
		}
		rwsols_login_maybe_cleanup($dir, $cfg, $opts);
		return $result;
	} catch (\Throwable $e) {
		return $open;
	}
}

// A correct password clears the client's counter.
function rwsols_login_record_success($key, array $cfg, array $opts = array())
{
	try {
		$cfg = rwsols_login_normalize_config($cfg);
		if ($cfg['max'] <= 0) {
			return;
		}
		$dir = rwsols_login_dir($opts);
		if ($dir === null) {
			return;
		}
		$file = rwsols_login_file($dir, $key);
		if (@is_file($file) || @is_link($file)) {
			@unlink($file);
		}
	} catch (\Throwable $e) {
		return;
	}
}

// About 1 call in RWSOLS_LOGIN_CLEANUP_ONE_IN removes expired counter files. Only files with our
// own prefix and .json suffix are touched. $opts['cleanup'] = true/false forces the decision.
function rwsols_login_maybe_cleanup($dir, array $cfg, array $opts = array())
{
	if (isset($opts['cleanup'])) {
		$run = (bool) $opts['cleanup'];
	} else {
		$run = random_int(1, RWSOLS_LOGIN_CLEANUP_ONE_IN) === 1;
	}
	if (!$run) {
		return 0;
	}
	$now = rwsols_clock($opts);
	$removed = 0;
	foreach ((array) @glob($dir . DIRECTORY_SEPARATOR . RWSOLS_LOGIN_FILE_PREFIX . '*.json') as $f) {
		if (@is_link($f) || !@is_file($f)) {
			continue;
		}
		$raw = @file_get_contents($f);
		$state = rwsols_login_state(is_string($raw) ? json_decode($raw, true) : null);
		if (rwsols_login_state_is_expired($state, $cfg, $now) && @unlink($f)) {
			$removed++;
		}
	}
	return $removed;
}
