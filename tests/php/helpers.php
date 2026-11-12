<?php
// Shared test helpers: RSA keys, JWKS documents, token builders, temp dirs, fake fetchers.

define('TH_TEAM', 'example.cloudflareaccess.com');
define('TH_AUD', 'aud-0000000000000000000000000000000000000000000000000000000000000000');
define('TH_NOW', 1700000000);

$GLOBALS['TH_TMPDIRS'] = array();

function th_b64url($bin)
{
	return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
}

// Returns a cached RSA key pair: array('priv', 'pub_pem', 'n', 'e', 'cert_pem').
function th_key($name)
{
	static $keys = array();
	if (!isset($keys[$name])) {
		$priv = openssl_pkey_new(array('private_key_bits' => 2048, 'private_key_type' => OPENSSL_KEYTYPE_RSA));
		if ($priv === false) {
			rt_fail('openssl_pkey_new failed: ' . openssl_error_string());
		}
		$d = openssl_pkey_get_details($priv);
		$cert_pem = null;
		$csr = openssl_csr_new(array('commonName' => 'example.com'), $priv, array('digest_alg' => 'sha256'));
		if ($csr !== false) {
			$x509 = openssl_csr_sign($csr, null, $priv, 1, array('digest_alg' => 'sha256'));
			if ($x509 !== false) {
				openssl_x509_export($x509, $cert_pem);
			}
		}
		$keys[$name] = array(
			'priv' => $priv,
			'pub_pem' => $d['key'],
			'n' => $d['rsa']['n'],
			'e' => $d['rsa']['e'],
			'cert_pem' => $cert_pem,
		);
	}
	return $keys[$name];
}

// $entries: array(kid => key name). Builds a certs document with both `keys` and `public_certs`.
function th_jwks(array $entries, $with_jwk = true, $with_certs = false)
{
	$doc = array('keys' => array(), 'public_cert' => null, 'public_certs' => array());
	foreach ($entries as $kid => $name) {
		$k = th_key($name);
		if ($with_jwk) {
			$doc['keys'][] = array('kid' => $kid, 'kty' => 'RSA', 'alg' => 'RS256', 'use' => 'sig',
				'e' => th_b64url($k['e']), 'n' => th_b64url($k['n']));
		}
		if ($with_certs) {
			if ($k['cert_pem'] === null) {
				rt_fail('could not create a test certificate');
			}
			$doc['public_certs'][] = array('kid' => $kid, 'cert' => $k['cert_pem']);
		}
	}
	return json_encode($doc);
}

function th_claims(array $overrides = array())
{
	return array_merge(array(
		'aud' => array(TH_AUD),
		'email' => 'user@example.com',
		'exp' => TH_NOW + 3600,
		'iat' => TH_NOW - 10,
		'nbf' => TH_NOW - 10,
		'iss' => 'https://' . TH_TEAM,
		'type' => 'app',
		'sub' => '00000000-0000-0000-0000-000000000000',
	), $overrides);
}

function th_token(array $claims, $key_name = 'a', $kid = 'kid-a', array $header_overrides = array())
{
	$header = array_merge(array('alg' => 'RS256', 'kid' => $kid, 'typ' => 'JWT'), $header_overrides);
	$input = th_b64url(json_encode($header)) . '.' . th_b64url(json_encode($claims));
	$sig = '';
	if (!openssl_sign($input, $sig, th_key($key_name)['priv'], OPENSSL_ALGO_SHA256)) {
		rt_fail('openssl_sign failed');
	}
	return $input . '.' . th_b64url($sig);
}

function th_hs256_token(array $claims, $secret, $kid = 'kid-a')
{
	$input = th_b64url(json_encode(array('alg' => 'HS256', 'kid' => $kid, 'typ' => 'JWT'))) . '.' . th_b64url(json_encode($claims));
	return $input . '.' . th_b64url(hash_hmac('sha256', $input, $secret, true));
}

function th_tmpdir()
{
	$dir = rtrim(sys_get_temp_dir(), '/\\') . DIRECTORY_SEPARATOR . 'rwsols-test-' . bin2hex(random_bytes(8));
	mkdir($dir, 0700);
	chmod($dir, 0700);
	$GLOBALS['TH_TMPDIRS'][] = $dir;
	return $dir;
}

function th_cleanup()
{
	foreach ($GLOBALS['TH_TMPDIRS'] as $dir) {
		foreach ((array) @glob($dir . DIRECTORY_SEPARATOR . '*') as $f) {
			@unlink($f);
		}
		@rmdir($dir);
	}
}

// A fake fetcher whose response can be changed and whose calls are counted.
class ThFetcher
{
	public $body;
	public $calls = 0;
	public $urls = array();

	public function __construct($body)
	{
		$this->body = $body;
	}

	public function __invoke($url)
	{
		$this->calls++;
		$this->urls[] = $url;
		return $this->body;
	}
}

class ThClock
{
	public $now;

	public function __construct($now = TH_NOW)
	{
		$this->now = $now;
	}

	public function __invoke()
	{
		return $this->now;
	}
}

function th_opts($fetcher, $clock, $dir = null)
{
	return array('fetcher' => $fetcher, 'clock' => $clock, 'cache_dir' => $dir === null ? th_tmpdir() : $dir);
}
