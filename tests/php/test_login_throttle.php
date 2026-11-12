<?php

// Builds options for the throttle helpers with a fake clock, a private temp dir and a captured log.
function lt_setup($max = 5, $lockout = 900)
{
	$ctx = new stdClass();
	$ctx->clock = new ThClock(TH_NOW);
	$ctx->dir = th_tmpdir();
	$ctx->log = array();
	$log = &$ctx->log;
	$ctx->opts = array(
		'dir' => $ctx->dir,
		'clock' => $ctx->clock,
		'cleanup' => false,
		'logger' => function ($line) use (&$log) {
			$log[] = $line;
		},
		'state_file' => $ctx->dir . DIRECTORY_SEPARATOR . 'ratelimit-test.json',
	);
	$ctx->cfg = array('max' => $max, 'window' => $lockout, 'lockout' => $lockout);
	return $ctx;
}

function lt_fail($ctx, $key, $n = 1)
{
	$r = null;
	for ($i = 0; $i < $n; $i++) {
		$r = rwsols_login_record_failure($key, $ctx->cfg, $ctx->opts);
	}
	return $r;
}

function lt_files($ctx)
{
	return (array) glob($ctx->dir . DIRECTORY_SEPARATOR . 'login-*.json');
}

rt_test('login: config defaults when unset', function () {
	rt_assert_same(array('max' => 5, 'window' => 900, 'lockout' => 900), rwsols_login_config(array()));
});

rt_test('login: config reads variables', function () {
	$c = rwsols_login_config(array('LOGIN_MAX_ATTEMPTS' => 3, 'LOGIN_LOCKOUT_SECONDS' => 60));
	rt_assert_same(array('max' => 3, 'window' => 60, 'lockout' => 60), $c);
	rt_assert_same(0, rwsols_login_config(array('LOGIN_MAX_ATTEMPTS' => 0))['max']);
	rt_assert_same(900, rwsols_login_config(array('LOGIN_LOCKOUT_SECONDS' => 'abc'))['lockout']);
});

rt_test('login: under the limit is not locked', function () {
	$ctx = lt_setup();
	$r = lt_fail($ctx, '203.0.113.5', 4);
	rt_assert_false($r['locked']);
	rt_assert_false(rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts)['locked']);
	rt_assert_same(array(), $ctx->log);
});

rt_test('login: the Nth failure locks and logs once', function () {
	$ctx = lt_setup();
	$r = lt_fail($ctx, '203.0.113.5', 5);
	rt_assert_true($r['locked']);
	rt_assert_same(900, $r['retry_after']);
	$c = rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts);
	rt_assert_true($c['locked']);
	rt_assert_same(array('login lockout client=203.0.113.5 seconds=900'), $ctx->log);
});

rt_test('login: lockout ends after the lockout time', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 5);
	$ctx->clock->now = TH_NOW + 899;
	$c = rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts);
	rt_assert_true($c['locked']);
	rt_assert_same(1, $c['retry_after']);
	$ctx->clock->now = TH_NOW + 900;
	rt_assert_false(rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts)['locked']);
	// A fresh count starts: four more failures do not lock again.
	rt_assert_false(lt_fail($ctx, '203.0.113.5', 4)['locked']);
	rt_assert_true(lt_fail($ctx, '203.0.113.5', 1)['locked']);
});

rt_test('login: attempts during lockout do not extend it', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 5);
	$ctx->clock->now = TH_NOW + 600;
	$r = lt_fail($ctx, '203.0.113.5', 3);
	rt_assert_true($r['locked']);
	rt_assert_same(300, $r['retry_after']);
	$ctx->clock->now = TH_NOW + 900;
	rt_assert_false(rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts)['locked']);
	rt_assert_same(1, count($ctx->log));
});

rt_test('login: success clears the counter', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 4);
	rwsols_login_record_success('203.0.113.5', $ctx->cfg, $ctx->opts);
	rt_assert_same(0, count(lt_files($ctx)));
	rt_assert_false(lt_fail($ctx, '203.0.113.5', 4)['locked']);
	rt_assert_true(lt_fail($ctx, '203.0.113.5', 1)['locked']);
});

rt_test('login: failures older than the window do not count', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 4);
	$ctx->clock->now = TH_NOW + 900;
	rt_assert_false(lt_fail($ctx, '203.0.113.5', 1)['locked']);
	rt_assert_false(lt_fail($ctx, '203.0.113.5', 3)['locked']);
	rt_assert_true(lt_fail($ctx, '203.0.113.5', 1)['locked']);
});

rt_test('login: max 0 disables throttling and touches no files', function () {
	$ctx = lt_setup(0);
	$ctx->opts['dir'] = $ctx->dir . DIRECTORY_SEPARATOR . 'does-not-exist';
	$r = lt_fail($ctx, '203.0.113.5', 20);
	rt_assert_false($r['locked']);
	rt_assert_false(rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts)['locked']);
	rwsols_login_record_success('203.0.113.5', $ctx->cfg, $ctx->opts);
	rt_assert_same(array(), $ctx->log);
	rt_assert_same(0, count(lt_files($ctx)));
	rt_assert_false(file_exists($ctx->opts['dir']));
});

rt_test('login: two clients are independent', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 5);
	rt_assert_true(rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts)['locked']);
	rt_assert_false(rwsols_login_check('203.0.113.6', $ctx->cfg, $ctx->opts)['locked']);
	rt_assert_false(lt_fail($ctx, '203.0.113.6', 4)['locked']);
});

rt_test('login: counter file name does not contain the client key', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 1);
	$files = lt_files($ctx);
	rt_assert_same(1, count($files));
	rt_assert_false(strpos(basename($files[0]), '203') !== false && strpos(basename($files[0]), '203.0.113.5') !== false);
	rt_assert_same(0, preg_match('/203\.0\.113/', basename($files[0])));
});

rt_test('login: IPv6 in the same /64 share a counter, another /64 does not', function () {
	$ctx = lt_setup();
	$a = rwsols_login_client_key(array('REMOTE_ADDR' => '2001:db8:0:1::5'), false);
	$b = rwsols_login_client_key(array('REMOTE_ADDR' => '2001:db8:0:1:ffff:ffff:ffff:ffff'), false);
	$c = rwsols_login_client_key(array('REMOTE_ADDR' => '2001:db8:0:2::5'), false);
	rt_assert_same('2001:db8:0:1::/64', $a);
	rt_assert_same($a, $b);
	rt_assert_true($a !== $c);
	lt_fail($ctx, $a, 3);
	lt_fail($ctx, $b, 2);
	rt_assert_true(rwsols_login_check($a, $ctx->cfg, $ctx->opts)['locked']);
	rt_assert_true(rwsols_login_check($b, $ctx->cfg, $ctx->opts)['locked']);
	rt_assert_false(rwsols_login_check($c, $ctx->cfg, $ctx->opts)['locked']);
});

rt_test('login: client key from REMOTE_ADDR when not tunnel, header ignored', function () {
	$server = array('REMOTE_ADDR' => '203.0.113.9', 'HTTP_CF_CONNECTING_IP' => '203.0.113.77');
	rt_assert_same('203.0.113.9', rwsols_login_client_key($server, false));
	rt_assert_same('203.0.113.9', rwsols_login_client_key($server, null));
});

rt_test('login: client key from CF-Connecting-IP on the tunnel origin', function () {
	$server = array('REMOTE_ADDR' => '127.0.0.1', 'HTTP_CF_CONNECTING_IP' => '203.0.113.77');
	rt_assert_same('203.0.113.77', rwsols_login_client_key($server, true));
	$server['HTTP_CF_CONNECTING_IP'] = '2001:db8:0:9::1';
	rt_assert_same('2001:db8:0:9::/64', rwsols_login_client_key($server, true));
});

rt_test('login: invalid or missing header falls back to REMOTE_ADDR on the tunnel origin', function () {
	$server = array('REMOTE_ADDR' => '127.0.0.1', 'HTTP_CF_CONNECTING_IP' => 'not-an-ip');
	rt_assert_same('127.0.0.1', rwsols_login_client_key($server, true));
	unset($server['HTTP_CF_CONNECTING_IP']);
	rt_assert_same('127.0.0.1', rwsols_login_client_key($server, true));
	$server['HTTP_CF_CONNECTING_IP'] = '203.0.113.1, 203.0.113.2';
	rt_assert_same('127.0.0.1', rwsols_login_client_key($server, true));
});

rt_test('login: invalid or missing address gives the key unknown', function () {
	rt_assert_same('unknown', rwsols_login_client_key(array(), false));
	rt_assert_same('unknown', rwsols_login_client_key(array('REMOTE_ADDR' => 'garbage'), false));
	rt_assert_same('unknown', rwsols_login_client_key(array('REMOTE_ADDR' => ''), true));
	rt_assert_same('unknown', rwsols_login_client_key(array('REMOTE_ADDR' => array('203.0.113.1')), false));
});

rt_test('login: unusable directory fails open and logs once per window', function () {
	$ctx = lt_setup();
	$ctx->opts['dir'] = $ctx->dir . DIRECTORY_SEPARATOR . 'missing';
	$r = lt_fail($ctx, '203.0.113.5', 10);
	rt_assert_false($r['locked']);
	rt_assert_false(rwsols_login_check('203.0.113.5', $ctx->cfg, $ctx->opts)['locked']);
	rwsols_login_record_success('203.0.113.5', $ctx->cfg, $ctx->opts);
	rt_assert_true(count($ctx->log) >= 1);
	rt_assert_true(strpos($ctx->log[0], 'login throttle disabled') === 0);
	rt_assert_false(strpos(implode("\n", $ctx->log), 'lockout') !== false);
});

rt_test('login: cleanup removes only expired counter files with our prefix', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.5', 2);
	lt_fail($ctx, '203.0.113.6', 5);
	$other = $ctx->dir . DIRECTORY_SEPARATOR . 'other-file.json';
	file_put_contents($other, '{}');
	$ctx->clock->now = TH_NOW + 901;
	$removed = rwsols_login_maybe_cleanup($ctx->dir, $ctx->cfg, array_merge($ctx->opts, array('cleanup' => true)));
	rt_assert_same(2, $removed);
	rt_assert_same(0, count(lt_files($ctx)));
	rt_assert_true(file_exists($other));
});

rt_test('login: cleanup keeps files that are still locked', function () {
	$ctx = lt_setup();
	lt_fail($ctx, '203.0.113.6', 5);
	$ctx->clock->now = TH_NOW + 100;
	$removed = rwsols_login_maybe_cleanup($ctx->dir, $ctx->cfg, array_merge($ctx->opts, array('cleanup' => true)));
	rt_assert_same(0, $removed);
	rt_assert_same(1, count(lt_files($ctx)));
});
