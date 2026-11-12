<?php

function tg_config(array $overrides = array())
{
	return rwsols_gate_config(array_merge(array(
		'ALLOWED_HOSTS' => array('wol.example.com', 'raspberrypi.local'),
		'TUNNEL_HOSTNAME' => 'wol.example.com',
		'ACCESS_TEAM_DOMAIN' => TH_TEAM,
		'ACCESS_AUD' => TH_AUD,
	), $overrides));
}

function tg_deps($fetcher = null)
{
	if ($fetcher === null) {
		$fetcher = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	}
	return array('access_opts' => th_opts($fetcher, new ThClock()));
}

function tg_server(array $s = array())
{
	return array_merge(array('REMOTE_ADDR' => '203.0.113.9', 'REQUEST_METHOD' => 'GET'), $s);
}

rt_test('gate: old config without any new keys behaves like legacy, no notices', function () {
	$cfg = rwsols_gate_config(array('USE_HTTPS' => false, 'APPROVED_HASH' => null));
	rt_assert_same(array(), $cfg['allowed_hosts']);
	rt_assert_same(null, $cfg['access_team_domain']);
	foreach (array(array('HTTP_HOST' => 'anything.example.net'), array(), array('HTTP_HOST' => "x\ny")) as $s) {
		$d = rwsols_gate_decide(tg_server($s), $cfg, tg_deps());
		rt_assert_same(200, $d['status']);
		rt_assert_same(false, $d['is_tunnel_origin']);
		rt_assert_same(null, $d['identity']);
	}
	$d = rwsols_gate_decide(array(), rwsols_gate_config(array()));
	rt_assert_same(200, $d['status']);
	$d = rwsols_gate_decide(array(), array());
	rt_assert_same(200, $d['status']);
	rt_assert_same(array(), rwsols_gate_config(null)['allowed_hosts']);
});

rt_test('gate: config with empty strings / NULLs is legacy', function () {
	$cfg = rwsols_gate_config(array('ALLOWED_HOSTS' => array(''), 'TUNNEL_HOSTNAME' => '',
		'ACCESS_TEAM_DOMAIN' => null, 'ACCESS_AUD' => '  '));
	rt_assert_same(array(), $cfg['allowed_hosts']);
	rt_assert_same(null, $cfg['tunnel_hostname']);
	rt_assert_same(null, $cfg['access_aud']);
	rt_assert_same(200, rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'x.example.net')), $cfg)['status']);
});

rt_test('gate: tunnel host without Access header -> 403', function () {
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com')), tg_config(), tg_deps());
	rt_assert_same(403, $d['status']);
	rt_assert_same(null, $d['identity']);
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'WOL.example.com.:443', 'HTTP_CF_ACCESS_JWT_ASSERTION' => '')), tg_config(), tg_deps());
	rt_assert_same(403, $d['status']);
});

rt_test('gate: tunnel host with valid Access token -> allow with identity', function () {
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com',
		'HTTP_CF_ACCESS_JWT_ASSERTION' => th_token(th_claims()))), tg_config(), tg_deps());
	rt_assert_same(200, $d['status']);
	rt_assert_same('user@example.com', $d['identity']);
});

rt_test('gate: tunnel host with invalid Access token -> 403', function () {
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com',
		'HTTP_CF_ACCESS_JWT_ASSERTION' => th_token(th_claims(array('aud' => 'other'))))), tg_config(), tg_deps());
	rt_assert_same(403, $d['status']);
	$throwing = array('verifier' => function () { throw new RuntimeException('boom'); });
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com',
		'HTTP_CF_ACCESS_JWT_ASSERTION' => 'x.y.z')), tg_config(), $throwing);
	rt_assert_same(403, $d['status']);
});

rt_test('gate: tunnel-origin listener without header -> 403 even with a LAN Host', function () {
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'raspberrypi.local', 'RWSOLS_TUNNEL_ORIGIN' => '1')), tg_config(), tg_deps());
	rt_assert_same(403, $d['status']);
	rt_assert_same(true, $d['is_tunnel_origin']);
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'raspberrypi.local', 'REDIRECT_RWSOLS_TUNNEL_ORIGIN' => '1')), tg_config(), tg_deps());
	rt_assert_same(403, $d['status']);
});

rt_test('gate: forged tunnel-origin request header does not mark origin', function () {
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'raspberrypi.local', 'HTTP_RWSOLS_TUNNEL_ORIGIN' => '1')), tg_config(), tg_deps());
	rt_assert_same(200, $d['status']);
	rt_assert_same(false, $d['is_tunnel_origin']);
});

rt_test('gate: LAN host unaffected when Access is configured', function () {
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'raspberrypi.local')), tg_config(), tg_deps());
	rt_assert_same(200, $d['status']);
	rt_assert_same(null, $d['identity']);
	rt_assert_same(false, $d['is_tunnel_origin']);
});

rt_test('gate: Access not configured -> tunnel host passes without header', function () {
	$cfg = tg_config(array('ACCESS_TEAM_DOMAIN' => null, 'ACCESS_AUD' => null));
	rt_assert_same(200, rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com')), $cfg, tg_deps())['status']);
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com', 'RWSOLS_TUNNEL_ORIGIN' => '1')), $cfg, tg_deps());
	rt_assert_same(200, $d['status']);
	rt_assert_same(true, $d['is_tunnel_origin']);
});

rt_test('gate: only one Access setting -> tunnel host 403, LAN host unaffected', function () {
	foreach (array(array('ACCESS_AUD' => null), array('ACCESS_TEAM_DOMAIN' => null)) as $o) {
		$cfg = tg_config($o);
		$tok = th_token(th_claims());
		$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com', 'HTTP_CF_ACCESS_JWT_ASSERTION' => $tok)), $cfg, tg_deps());
		rt_assert_same(403, $d['status']);
		rt_assert_true(strpos($d['reason'], 'misconfigured') !== false);
		rt_assert_same(200, rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'raspberrypi.local')), $cfg, tg_deps())['status']);
	}
});

rt_test('gate: service token claims (common_name, no email) yield identity', function () {
	$c = th_claims(array('common_name' => 'svc-0000.access'));
	unset($c['email']);
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com',
		'HTTP_CF_ACCESS_JWT_ASSERTION' => th_token($c))), tg_config(), tg_deps());
	rt_assert_same(200, $d['status']);
	rt_assert_same('svc-0000.access', $d['identity']);
});

rt_test('gate: host check runs before Access check', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$d = rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'evil.example.net',
		'HTTP_CF_ACCESS_JWT_ASSERTION' => th_token(th_claims()))), tg_config(), tg_deps($f));
	rt_assert_same(421, $d['status']);
	rt_assert_same(0, $f->calls, 'no key fetch for disallowed host');
	$d = rwsols_gate_decide(tg_server(array('RWSOLS_TUNNEL_ORIGIN' => '1',
		'HTTP_CF_ACCESS_JWT_ASSERTION' => th_token(th_claims()))), tg_config(), tg_deps($f));
	rt_assert_same(421, $d['status'], 'missing Host rejected first');
});

rt_test('gate: tunnel hostname is not implicitly allowed', function () {
	$cfg = tg_config(array('ALLOWED_HOSTS' => array('raspberrypi.local'), 'ACCESS_TEAM_DOMAIN' => null, 'ACCESS_AUD' => null));
	rt_assert_same(421, rwsols_gate_decide(tg_server(array('HTTP_HOST' => 'wol.example.com')), $cfg)['status']);
});

rt_test('gate: ALLOW_LOCALHOST via config', function () {
	$cfg = tg_config(array('ALLOW_LOCALHOST' => true));
	rt_assert_same(200, rwsols_gate_decide(tg_server(array('HTTP_HOST' => '[::1]:8080')), $cfg)['status']);
	rt_assert_same(421, rwsols_gate_decide(tg_server(array('HTTP_HOST' => '[::1]:8080')), tg_config())['status']);
});
