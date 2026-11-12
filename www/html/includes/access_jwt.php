<?php
/*
Remote Wake/Sleep-On-LAN Server
https://github.com/sciguy14/Remote-Wake-Sleep-On-LAN-Server
License: GPL v3 (http://www.gnu.org/licenses/gpl.html)

Cloudflare Access JWT (Cf-Access-Jwt-Assertion) verification. RS256 only.
This file only defines functions; requesting it directly does nothing.
*/

require_once(__DIR__ . '/host_guard.php');

if (!defined('RWSOLS_ACCESS_MAX_TOKEN_LEN')) {
	define('RWSOLS_ACCESS_MAX_TOKEN_LEN', 8192);
	define('RWSOLS_ACCESS_LEEWAY', 60);
	define('RWSOLS_ACCESS_CACHE_TTL', 3600);
	define('RWSOLS_ACCESS_MIN_REFETCH_INTERVAL', 60);
	define('RWSOLS_ACCESS_MAX_CERTS_BYTES', 262144);
}

// Strict base64url: URL-safe alphabet, no padding, canonical encoding only.
function rwsols_b64url_decode($s)
{
	if (!is_string($s) || $s === '' || !preg_match('/^[A-Za-z0-9_-]+$/D', $s) || strlen($s) % 4 === 1) {
		return false;
	}
	$bin = base64_decode(strtr($s, '-_', '+/') . str_repeat('=', (4 - strlen($s) % 4) % 4), true);
	if ($bin === false || rtrim(strtr(base64_encode($bin), '+/', '-_'), '=') !== $s) {
		return false;
	}
	return $bin;
}

// The team domain is interpolated into a URL, so it must be a plain DNS name with no scheme,
// port, path or userinfo.
function rwsols_access_valid_team_domain($domain)
{
	if (!is_string($domain) || strlen($domain) > 253) {
		return false;
	}
	return (bool) preg_match('/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/D', $domain);
}

function rwsols_der_length($len)
{
	if ($len < 0x80) {
		return chr($len);
	}
	$s = '';
	while ($len > 0) {
		$s = chr($len & 0xff) . $s;
		$len >>= 8;
	}
	return chr(0x80 | strlen($s)) . $s;
}

function rwsols_der_tlv($tag, $body)
{
	return chr($tag) . rwsols_der_length(strlen($body)) . $body;
}

function rwsols_der_uint($bin)
{
	$bin = ltrim($bin, "\0");
	if ($bin === '') {
		$bin = "\0";
	}
	if (ord($bin[0]) > 0x7f) {
		$bin = "\0" . $bin;
	}
	return rwsols_der_tlv(0x02, $bin);
}

// Builds a PEM SubjectPublicKeyInfo from base64url JWK modulus/exponent, or returns false.
function rwsols_jwk_to_pem($n_b64, $e_b64)
{
	$n = rwsols_b64url_decode($n_b64);
	$e = rwsols_b64url_decode($e_b64);
	if ($n === false || $e === false || strlen($e) > 8) {
		return false;
	}
	$rsa_key = rwsols_der_tlv(0x30, rwsols_der_uint($n) . rwsols_der_uint($e));
	// AlgorithmIdentifier: rsaEncryption OID 1.2.840.113549.1.1.1, NULL parameters
	$alg = rwsols_der_tlv(0x30, "\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01\x05\x00");
	$spki = rwsols_der_tlv(0x30, $alg . rwsols_der_tlv(0x03, "\0" . $rsa_key));
	return "-----BEGIN PUBLIC KEY-----\n" . chunk_split(base64_encode($spki), 64, "\n") . "-----END PUBLIC KEY-----\n";
}

// Returns a normalized PEM public key if $pem (public key or certificate) is an RSA key of at
// least 2048 bits, else false.
function rwsols_access_normalize_rsa_pem($pem)
{
	if (!is_string($pem) || $pem === '') {
		return false;
	}
	$key = @openssl_pkey_get_public($pem);
	if ($key === false) {
		return false;
	}
	$details = @openssl_pkey_get_details($key);
	if (!is_array($details) || !isset($details['type'], $details['bits'], $details['key'])
		|| $details['type'] !== OPENSSL_KEYTYPE_RSA || $details['bits'] < 2048) {
		return false;
	}
	return $details['key'];
}

// Parses the certs endpoint JSON into array(kid => PEM public key). JWK `keys` are preferred;
// `public_certs` is used only for kids not present there.
function rwsols_access_parse_certs($json)
{
	$keys = array();
	if (!is_string($json) || $json === '') {
		return $keys;
	}
	$doc = json_decode($json, true);
	if (!is_array($doc)) {
		return $keys;
	}
	if (isset($doc['keys']) && is_array($doc['keys'])) {
		foreach ($doc['keys'] as $jwk) {
			if (!is_array($jwk) || !isset($jwk['kid'], $jwk['kty'], $jwk['n'], $jwk['e'])
				|| !is_string($jwk['kid']) || $jwk['kid'] === '' || $jwk['kty'] !== 'RSA'
				|| !is_string($jwk['n']) || !is_string($jwk['e'])) {
				continue;
			}
			if (isset($jwk['alg']) && $jwk['alg'] !== 'RS256') {
				continue;
			}
			if (isset($jwk['use']) && $jwk['use'] !== 'sig') {
				continue;
			}
			$pem = rwsols_access_normalize_rsa_pem(rwsols_jwk_to_pem($jwk['n'], $jwk['e']));
			if ($pem !== false) {
				$keys[$jwk['kid']] = $pem;
			}
		}
	}
	if (isset($doc['public_certs']) && is_array($doc['public_certs'])) {
		foreach ($doc['public_certs'] as $c) {
			if (!is_array($c) || !isset($c['kid'], $c['cert']) || !is_string($c['kid'])
				|| $c['kid'] === '' || isset($keys[$c['kid']])) {
				continue;
			}
			$pem = rwsols_access_normalize_rsa_pem($c['cert']);
			if ($pem !== false) {
				$keys[$c['kid']] = $pem;
			}
		}
	}
	return $keys;
}

// Default fetcher: HTTPS GET with TLS verification, short timeouts and no redirects.
function rwsols_access_http_fetch($url)
{
	if (!function_exists('curl_init')) {
		return false;
	}
	$ch = curl_init();
	if ($ch === false) {
		return false;
	}
	curl_setopt($ch, CURLOPT_URL, $url);
	curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
	curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
	curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);
	curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, 2);
	curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
	curl_setopt($ch, CURLOPT_TIMEOUT, 5);
	if (defined('CURLOPT_PROTOCOLS') && defined('CURLPROTO_HTTPS')) {
		curl_setopt($ch, CURLOPT_PROTOCOLS, CURLPROTO_HTTPS);
	}
	curl_setopt($ch, CURLOPT_HTTPHEADER, array('Accept: application/json'));
	$body = curl_exec($ch);
	$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
	curl_close($ch);
	if (!is_string($body) || $code !== 200 || strlen($body) > RWSOLS_ACCESS_MAX_CERTS_BYTES) {
		return false;
	}
	return $body;
}

function rwsols_access_cache_load($file)
{
	$empty = array('fetched_at' => 0, 'last_attempt' => 0, 'keys' => array());
	if ($file === null || !@is_file($file) || @is_link($file)) {
		return $empty;
	}
	$raw = @file_get_contents($file);
	$data = is_string($raw) ? json_decode($raw, true) : null;
	if (!is_array($data) || !isset($data['fetched_at'], $data['last_attempt'], $data['keys'])
		|| !is_int($data['fetched_at']) || !is_int($data['last_attempt']) || !is_array($data['keys'])) {
		return $empty;
	}
	$keys = array();
	foreach ($data['keys'] as $kid => $pem) {
		if ((is_string($kid) || is_int($kid)) && is_string($pem)) {
			$keys[(string) $kid] = $pem;
		}
	}
	$data['keys'] = $keys;
	return $data;
}

function rwsols_access_cache_save($file, array $state)
{
	if ($file === null) {
		return false;
	}
	try {
		$tmp = $file . '.' . bin2hex(random_bytes(6)) . '.tmp';
		if (@file_put_contents($tmp, json_encode($state), LOCK_EX) === false) {
			return false;
		}
		@chmod($tmp, 0600);
		if (!@rename($tmp, $file)) {
			@unlink($tmp);
			return false;
		}
		return true;
	} catch (\Throwable $e) {
		return false;
	}
}

// Returns the PEM public key for $kid, fetching the team's certs when the cache is missing,
// expired, or lacks $kid. Network fetches (of any kind) happen at most once per
// RWSOLS_ACCESS_MIN_REFETCH_INTERVAL seconds per team domain. Returns false if unavailable.
// Options: fetcher (callable url => string|false), clock (callable), cache_dir (string),
// cache_ttl, min_refetch_interval.
function rwsols_access_get_key($team_domain, $kid, array $opts = array())
{
	static $memory = array();
	if (!rwsols_access_valid_team_domain($team_domain) || !is_string($kid) || $kid === '') {
		return false;
	}
	$now = rwsols_clock($opts);
	$ttl = isset($opts['cache_ttl']) ? (int) $opts['cache_ttl'] : RWSOLS_ACCESS_CACHE_TTL;
	$min_interval = isset($opts['min_refetch_interval']) ? (int) $opts['min_refetch_interval'] : RWSOLS_ACCESS_MIN_REFETCH_INTERVAL;
	$fetcher = (isset($opts['fetcher']) && is_callable($opts['fetcher'])) ? $opts['fetcher'] : 'rwsols_access_http_fetch';

	$dir = array_key_exists('cache_dir', $opts) ? $opts['cache_dir'] : rwsols_private_tmp_dir();
	$file = null;
	if (is_string($dir) && rwsols_dir_is_private($dir)) {
		$file = rtrim($dir, '/\\') . DIRECTORY_SEPARATOR . 'access-certs-' . sha1($team_domain) . '.json';
	}
	$mem_key = $team_domain . '|' . (is_string($dir) ? $dir : '');

	if ($file !== null) {
		$state = rwsols_access_cache_load($file);
	} elseif (isset($memory[$mem_key])) {
		$state = $memory[$mem_key];
	} else {
		$state = array('fetched_at' => 0, 'last_attempt' => 0, 'keys' => array());
	}

	$fresh = $state['fetched_at'] > 0 && $now >= $state['fetched_at'] && ($now - $state['fetched_at']) < $ttl;
	if ($fresh && isset($state['keys'][$kid])) {
		return $state['keys'][$kid];
	}

	$may_fetch = $state['last_attempt'] === 0 || $now < $state['last_attempt']
		|| ($now - $state['last_attempt']) >= $min_interval;
	if (!$may_fetch) {
		return false;
	}

	// Record the attempt before fetching so concurrent or repeated bad tokens back off.
	$state['last_attempt'] = $now;
	if ($file !== null) {
		rwsols_access_cache_save($file, $state);
	}
	$memory[$mem_key] = $state;

	try {
		$body = call_user_func($fetcher, 'https://' . $team_domain . '/cdn-cgi/access/certs');
	} catch (\Throwable $e) {
		$body = false;
	}
	$keys = rwsols_access_parse_certs(is_string($body) ? $body : '');
	if (count($keys) === 0) {
		return false;
	}
	$state['keys'] = $keys;
	$state['fetched_at'] = $now;
	if ($file !== null) {
		rwsols_access_cache_save($file, $state);
	}
	$memory[$mem_key] = $state;
	return isset($keys[$kid]) ? $keys[$kid] : false;
}

function rwsols_access_decode_json_object($bin)
{
	$obj = json_decode($bin, false, 32);
	if (!is_object($obj)) {
		return false;
	}
	$arr = json_decode($bin, true, 32);
	return is_array($arr) ? $arr : false;
}

// Verifies a Cloudflare Access JWT. Returns array('ok' => bool, 'claims' => array|null,
// 'error' => string). The error string is for logs only, never for the HTTP response.
function rwsols_access_verify($token, $team_domain, $aud, array $opts = array())
{
	$fail = function ($why) {
		return array('ok' => false, 'claims' => null, 'error' => $why);
	};
	if (!rwsols_access_valid_team_domain($team_domain)) {
		return $fail('invalid team domain');
	}
	if (!is_string($aud) || $aud === '') {
		return $fail('invalid aud config');
	}
	if (!is_string($token) || $token === '') {
		return $fail('missing token');
	}
	if (strlen($token) > RWSOLS_ACCESS_MAX_TOKEN_LEN) {
		return $fail('token too large');
	}
	$parts = explode('.', $token);
	if (count($parts) !== 3) {
		return $fail('malformed token');
	}
	$header_bin = rwsols_b64url_decode($parts[0]);
	$payload_bin = rwsols_b64url_decode($parts[1]);
	$sig = rwsols_b64url_decode($parts[2]);
	if ($header_bin === false || $payload_bin === false || $sig === false) {
		return $fail('malformed token encoding');
	}
	$header = rwsols_access_decode_json_object($header_bin);
	$claims = rwsols_access_decode_json_object($payload_bin);
	if ($header === false || $claims === false) {
		return $fail('malformed token json');
	}
	if (!isset($header['alg']) || $header['alg'] !== 'RS256') {
		return $fail('unsupported alg');
	}
	if (isset($header['crit'])) {
		return $fail('unsupported crit header');
	}
	if (!isset($header['kid']) || !is_string($header['kid']) || $header['kid'] === '' || strlen($header['kid']) > 256) {
		return $fail('missing kid');
	}

	$pem = rwsols_access_get_key($team_domain, $header['kid'], $opts);
	if ($pem === false) {
		return $fail('signing key unavailable');
	}
	$pub = @openssl_pkey_get_public($pem);
	if ($pub === false) {
		return $fail('signing key unusable');
	}
	if (@openssl_verify($parts[0] . '.' . $parts[1], $sig, $pub, OPENSSL_ALGO_SHA256) !== 1) {
		while (@openssl_error_string() !== false) {
		}
		return $fail('bad signature');
	}

	$now = rwsols_clock($opts);
	$leeway = RWSOLS_ACCESS_LEEWAY;
	if (!isset($claims['exp']) || !(is_int($claims['exp']) || is_float($claims['exp']))) {
		return $fail('missing exp');
	}
	if ($now > $claims['exp'] + $leeway) {
		return $fail('expired');
	}
	if (array_key_exists('nbf', $claims)) {
		if (!(is_int($claims['nbf']) || is_float($claims['nbf']))) {
			return $fail('invalid nbf');
		}
		if ($now + $leeway < $claims['nbf']) {
			return $fail('not yet valid');
		}
	}
	if (!isset($claims['iss']) || $claims['iss'] !== 'https://' . $team_domain) {
		return $fail('wrong iss');
	}
	$aud_ok = false;
	if (isset($claims['aud'])) {
		if (is_string($claims['aud'])) {
			$aud_ok = ($claims['aud'] === $aud);
		} elseif (is_array($claims['aud'])) {
			$aud_ok = in_array($aud, $claims['aud'], true);
		}
	}
	if (!$aud_ok) {
		return $fail('wrong aud');
	}
	return array('ok' => true, 'claims' => $claims, 'error' => '');
}

// Identity for audit logging: email for users, common_name for service tokens.
function rwsols_access_identity($claims)
{
	if (!is_array($claims)) {
		return null;
	}
	foreach (array('email', 'common_name') as $k) {
		if (isset($claims[$k]) && is_string($claims[$k]) && $claims[$k] !== '') {
			return $claims[$k];
		}
	}
	return null;
}
