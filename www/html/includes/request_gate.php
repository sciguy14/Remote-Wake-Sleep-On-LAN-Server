<?php
/*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)

Request gate: Host allowlist (421), then Cloudflare Access for tunnel requests (403).
This file only defines functions; index.php calls rwsols_request_gate_run().
*/

require_once(__DIR__ . '/host_guard.php');
require_once(__DIR__ . '/access_jwt.php');

function rwsols_gate_config_string($vars, $name)
{
	if (!isset($vars[$name]) || !is_string($vars[$name])) {
		return null;
	}
	$v = trim($vars[$name]);
	if ($name !== 'ACCESS_AUD') {
		$v = strtolower($v);
	}
	return $v === '' ? null : $v;
}

// Reads the optional security settings from an array of config variables (normally $GLOBALS),
// with defaults that reproduce the legacy behavior when a setting is absent.
function rwsols_gate_config($vars)
{
	if (!is_array($vars)) {
		$vars = array();
	}
	$hosts = array();
	if (isset($vars['ALLOWED_HOSTS'])) {
		$raw = is_array($vars['ALLOWED_HOSTS']) ? $vars['ALLOWED_HOSTS'] : array($vars['ALLOWED_HOSTS']);
		foreach ($raw as $h) {
			if (is_string($h) && trim($h) !== '') {
				$hosts[] = trim($h);
			}
		}
	}
	return array(
		'allowed_hosts' => $hosts,
		'allow_localhost' => !empty($vars['ALLOW_LOCALHOST']),
		'log_host_rejections' => !empty($vars['LOG_HOST_REJECTIONS']),
		'tunnel_hostname' => rwsols_gate_config_string($vars, 'TUNNEL_HOSTNAME'),
		'access_team_domain' => rwsols_gate_config_string($vars, 'ACCESS_TEAM_DOMAIN'),
		'access_aud' => rwsols_gate_config_string($vars, 'ACCESS_AUD'),
	);
}

function rwsols_gate_is_tunnel_origin($server)
{
	foreach (array('RWSOLS_TUNNEL_ORIGIN', 'REDIRECT_RWSOLS_TUNNEL_ORIGIN') as $k) {
		if (isset($server[$k]) && $server[$k] === '1') {
			return true;
		}
	}
	return false;
}

// Pure decision function. Returns array(
//   'status' => 200 | 421 | 403, 'is_tunnel_origin' => bool, 'identity' => string|null,
//   'reason' => string (for logs only)).
// $deps: 'verifier' callable($token, $team_domain, $aud) returning rwsols_access_verify()'s
// result shape, or 'access_opts' passed to rwsols_access_verify().
function rwsols_gate_decide(array $server, array $config, array $deps = array())
{
	$config = array_merge(rwsols_gate_config(array()), $config);
	$is_origin = rwsols_gate_is_tunnel_origin($server);
	$host = isset($server['HTTP_HOST']) ? $server['HTTP_HOST'] : null;
	$decision = array('status' => 200, 'is_tunnel_origin' => $is_origin, 'identity' => null, 'reason' => '');

	if (!rwsols_host_is_allowed($host, $config['allowed_hosts'], $config['allow_localhost'])) {
		$decision['status'] = 421;
		$decision['reason'] = 'host not allowed';
		return $decision;
	}

	$is_tunnel_request = $is_origin;
	if (!$is_tunnel_request && $config['tunnel_hostname'] !== null) {
		$tunnel_host = rwsols_normalize_host($config['tunnel_hostname']);
		$is_tunnel_request = ($tunnel_host !== null && rwsols_normalize_host($host) === $tunnel_host);
	}
	if (!$is_tunnel_request) {
		return $decision;
	}

	$domain = $config['access_team_domain'];
	$aud = $config['access_aud'];
	if ($domain === null && $aud === null) {
		return $decision;
	}
	if ($domain === null || $aud === null) {
		$decision['status'] = 403;
		$decision['reason'] = 'access misconfigured: set both ACCESS_TEAM_DOMAIN and ACCESS_AUD';
		return $decision;
	}

	$token = isset($server['HTTP_CF_ACCESS_JWT_ASSERTION']) ? $server['HTTP_CF_ACCESS_JWT_ASSERTION'] : null;
	if (!is_string($token) || $token === '') {
		$decision['status'] = 403;
		$decision['reason'] = 'missing access token';
		return $decision;
	}
	try {
		if (isset($deps['verifier']) && is_callable($deps['verifier'])) {
			$result = call_user_func($deps['verifier'], $token, $domain, $aud);
		} else {
			$opts = (isset($deps['access_opts']) && is_array($deps['access_opts'])) ? $deps['access_opts'] : array();
			$result = rwsols_access_verify($token, $domain, $aud, $opts);
		}
	} catch (\Throwable $e) {
		$result = null;
	}
	if (!is_array($result) || !isset($result['ok']) || $result['ok'] !== true) {
		$decision['status'] = 403;
		$decision['reason'] = 'access token rejected: ' . ((is_array($result) && isset($result['error'])) ? $result['error'] : 'verifier error');
		return $decision;
	}
	$decision['identity'] = rwsols_access_identity(isset($result['claims']) ? $result['claims'] : null);
	return $decision;
}

function rwsols_gate_respond_empty($status)
{
	while (ob_get_level() > 0) {
		@ob_end_clean();
	}
	@ini_set('default_mimetype', '');
	if (!headers_sent()) {
		header_remove('X-Powered-By');
		http_response_code($status);
		header('Content-Length: 0');
	}
	exit;
}

// Applies the gate to the current request: exits with 421/403 when rejected, otherwise sets
// $RWSOLS_IS_TUNNEL_ORIGIN and $RWSOLS_ACCESS_IDENTITY for index.php.
function rwsols_request_gate_run()
{
	$config = rwsols_gate_config($GLOBALS);
	$server = $_SERVER;
	$decision = rwsols_gate_decide($server, $config);
	$GLOBALS['RWSOLS_IS_TUNNEL_ORIGIN'] = $decision['is_tunnel_origin'];
	$GLOBALS['RWSOLS_ACCESS_IDENTITY'] = $decision['identity'];
	$ip = isset($server['REMOTE_ADDR']) ? $server['REMOTE_ADDR'] : null;

	if ($decision['status'] === 421) {
		if ($config['log_host_rejections']) {
			rwsols_log_host_rejection($ip, isset($server['HTTP_HOST']) ? $server['HTTP_HOST'] : null);
		}
		rwsols_gate_respond_empty(421);
	}
	if ($decision['status'] === 403) {
		rwsols_rate_limited_log('access', 'denied tunnel request from ' . rwsols_sanitize_log_value($ip, 64)
			. ': ' . rwsols_sanitize_log_value($decision['reason'], 200));
		rwsols_gate_respond_empty(403);
	}
	if ($decision['identity'] !== null && isset($server['REQUEST_METHOD']) && $server['REQUEST_METHOD'] === 'POST') {
		$action = (isset($_POST['submitbutton']) && is_string($_POST['submitbutton'])) ? $_POST['submitbutton'] : null;
		$computer = (isset($_GET['computer']) && is_string($_GET['computer'])) ? $_GET['computer'] : null;
		rwsols_syslog('access identity "' . rwsols_sanitize_log_value($decision['identity'])
			. '" from ' . rwsols_sanitize_log_value($ip, 64) . ' POST action "'
			. rwsols_sanitize_log_value($action, 32) . '" computer "' . rwsols_sanitize_log_value($computer, 16) . '"');
	}
}
