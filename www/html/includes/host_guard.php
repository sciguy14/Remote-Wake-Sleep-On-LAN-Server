<?php
/*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)

Host allowlist matching and rate-limited rejection logging.
This file only defines functions; requesting it directly does nothing.
*/

if (!defined('RWSOLS_LOG_WINDOW')) {
	define('RWSOLS_LOG_WINDOW', 60);
	define('RWSOLS_LOG_MAX_PER_WINDOW', 10);
}

// Returns the normalized host (lowercase, no port, one trailing dot removed) or NULL if the
// value is missing or contains anything other than a plain host name / IP literal.
function rwsols_normalize_host($host)
{
	if (!is_string($host) || $host === '' || strlen($host) > 300) {
		return null;
	}
	$h = strtolower($host);
	if ($h[0] === '[') {
		if (!preg_match('/^(\[[0-9a-f:.]+\])(?::[0-9]{1,5})?$/D', $h, $m)) {
			return null;
		}
		if (filter_var(substr($m[1], 1, -1), FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) === false) {
			return null;
		}
		return $m[1];
	}
	if (!preg_match('/^([a-z0-9._-]+)(?::[0-9]{1,5})?$/D', $h, $m)) {
		return null;
	}
	$name = $m[1];
	if (substr($name, -1) === '.') {
		$name = substr($name, 0, -1);
	}
	if ($name === '' || strpos($name, '..') !== false || $name[0] === '.') {
		return null;
	}
	return $name;
}

// Normalizes the configured entries; entries that are not valid host names are dropped (they can
// never match anything, e.g. "*.example.com").
function rwsols_host_allowlist($allowed_hosts, $allow_localhost = false)
{
	$out = array();
	if (is_array($allowed_hosts)) {
		foreach ($allowed_hosts as $entry) {
			$n = rwsols_normalize_host(is_string($entry) ? trim($entry) : null);
			if ($n !== null) {
				$out[$n] = true;
			}
		}
	}
	if ($allow_localhost) {
		$out['localhost'] = true;
		$out['127.0.0.1'] = true;
		$out['[::1]'] = true;
	}
	return array_keys($out);
}

// An empty $allowed_hosts disables Host checking entirely (legacy behavior).
function rwsols_host_is_allowed($host, $allowed_hosts, $allow_localhost = false)
{
	if (!is_array($allowed_hosts) || count($allowed_hosts) === 0) {
		return true;
	}
	$n = rwsols_normalize_host($host);
	if ($n === null) {
		return false;
	}
	return in_array($n, rwsols_host_allowlist($allowed_hosts, $allow_localhost), true);
}

function rwsols_sanitize_log_value($value, $max_len = 128)
{
	if ($value === null) {
		return '(none)';
	}
	if (!is_string($value)) {
		return '(invalid)';
	}
	$v = preg_replace('/[^\x20-\x7e]|"|\\\\/', '?', $value);
	if (!is_string($v)) {
		return '(invalid)';
	}
	if (strlen($v) > $max_len) {
		$v = substr($v, 0, $max_len) . '...';
	}
	return $v;
}

function rwsols_syslog($line, $priority = LOG_NOTICE)
{
	if (!function_exists('syslog')) {
		return;
	}
	@openlog('rwsols', LOG_PID, defined('LOG_AUTH') ? LOG_AUTH : LOG_USER);
	@syslog($priority, $line);
}

function rwsols_clock(array $opts)
{
	if (isset($opts['clock']) && is_callable($opts['clock'])) {
		return (int) call_user_func($opts['clock']);
	}
	return time();
}

// A directory under the system temp dir that only the current user can write, or NULL.
// Files in a shared, world-writable temp dir could be planted by other local users.
function rwsols_private_tmp_dir()
{
	$uid = function_exists('posix_geteuid') ? (string) posix_geteuid() : 'php';
	$dir = rtrim(sys_get_temp_dir(), '/\\') . DIRECTORY_SEPARATOR . 'rwsols-' . $uid;
	if (!@is_dir($dir) && !@is_link($dir)) {
		@mkdir($dir, 0700);
	}
	return rwsols_dir_is_private($dir) ? $dir : null;
}

function rwsols_dir_is_private($dir)
{
	if (!is_string($dir) || $dir === '' || @is_link($dir) || !@is_dir($dir)) {
		return false;
	}
	if (DIRECTORY_SEPARATOR !== '\\') {
		$perms = @fileperms($dir);
		if ($perms === false || ($perms & 0022) !== 0) {
			return false;
		}
		if (function_exists('posix_geteuid') && @fileowner($dir) !== posix_geteuid()) {
			return false;
		}
	}
	return @is_writable($dir);
}

// Logs $line unless more than RWSOLS_LOG_MAX_PER_WINDOW lines were logged in the current
// window for this bucket. When a window with suppressed lines rolls over, one summary line is
// logged. Never throws or emits warnings; falls back to per-process state if the state file
// cannot be used. Options: logger (callable), clock (callable), state_file (string).
// Returns true if $line itself was logged.
function rwsols_rate_limited_log($bucket, $line, array $opts = array())
{
	static $memory = array();
	try {
		$logger = (isset($opts['logger']) && is_callable($opts['logger'])) ? $opts['logger'] : 'rwsols_syslog';
		$now = rwsols_clock($opts);
		if (isset($opts['state_file'])) {
			$file = $opts['state_file'];
		} else {
			$dir = rwsols_private_tmp_dir();
			$file = ($dir === null) ? null : $dir . DIRECTORY_SEPARATOR . 'ratelimit-' . preg_replace('/[^a-z0-9_-]/', '', strtolower($bucket)) . '.json';
		}
		$mem_key = $bucket . '|' . (is_string($file) ? $file : '');

		$state = null;
		$fh = false;
		if (is_string($file) && $file !== '' && !@is_link($file)) {
			$fh = @fopen($file, 'c+');
			if ($fh !== false) {
				@flock($fh, LOCK_EX);
				$raw = @stream_get_contents($fh);
				$decoded = is_string($raw) ? json_decode($raw, true) : null;
				if (is_array($decoded)) {
					$state = $decoded;
				}
			}
		}
		if ($fh === false && isset($memory[$mem_key])) {
			$state = $memory[$mem_key];
		}
		if (!is_array($state) || !isset($state['window_start'], $state['count'], $state['suppressed'])
			|| !is_int($state['window_start']) || !is_int($state['count']) || !is_int($state['suppressed'])) {
			$state = array('window_start' => $now, 'count' => 0, 'suppressed' => 0);
		}

		$emit = array();
		if ($now - $state['window_start'] >= RWSOLS_LOG_WINDOW || $now < $state['window_start']) {
			if ($state['suppressed'] > 0) {
				$emit[] = $state['suppressed'] . ' more ' . $bucket . ' log lines suppressed';
			}
			$state = array('window_start' => $now, 'count' => 0, 'suppressed' => 0);
		}
		$logged = false;
		if ($state['count'] < RWSOLS_LOG_MAX_PER_WINDOW) {
			$state['count']++;
			$emit[] = $line;
			$logged = true;
		} else {
			$state['suppressed']++;
		}

		if ($fh !== false) {
			@ftruncate($fh, 0);
			@rewind($fh);
			@fwrite($fh, json_encode($state));
			@fflush($fh);
			@flock($fh, LOCK_UN);
			@fclose($fh);
		} else {
			$memory[$mem_key] = $state;
		}

		foreach ($emit as $l) {
			call_user_func($logger, $l);
		}
		return $logged;
	} catch (\Throwable $e) {
		return false;
	}
}

function rwsols_log_host_rejection($remote_addr, $host, array $opts = array())
{
	$line = 'rejected request with disallowed Host "' . rwsols_sanitize_log_value($host)
		. '" from ' . rwsols_sanitize_log_value($remote_addr, 64);
	return rwsols_rate_limited_log('host', $line, $opts);
}
