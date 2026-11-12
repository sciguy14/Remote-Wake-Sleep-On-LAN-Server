<?php

$list = array('wol.example.com', 'raspberrypi.local');

rt_test('host: allowed', function () use ($list) {
	rt_assert_true(rwsols_host_is_allowed('wol.example.com', $list));
	rt_assert_true(rwsols_host_is_allowed('raspberrypi.local', $list));
});

rt_test('host: allowed with port', function () use ($list) {
	rt_assert_true(rwsols_host_is_allowed('wol.example.com:443', $list));
	rt_assert_true(rwsols_host_is_allowed('wol.example.com:8080', $list));
});

rt_test('host: uppercase', function () use ($list) {
	rt_assert_true(rwsols_host_is_allowed('WOL.Example.COM', $list));
	rt_assert_true(rwsols_host_is_allowed('wol.example.com', array('WOL.EXAMPLE.COM')));
});

rt_test('host: trailing dot', function () use ($list) {
	rt_assert_true(rwsols_host_is_allowed('wol.example.com.', $list));
	rt_assert_true(rwsols_host_is_allowed('wol.example.com.:80', $list));
	rt_assert_true(rwsols_host_is_allowed('wol.example.com', array('wol.example.com.')));
	rt_assert_false(rwsols_host_is_allowed('wol.example.com..', $list));
});

rt_test('host: disallowed', function () use ($list) {
	rt_assert_false(rwsols_host_is_allowed('attacker.example.net', $list));
});

rt_test('host: missing or empty Host rejected', function () use ($list) {
	rt_assert_false(rwsols_host_is_allowed(null, $list));
	rt_assert_false(rwsols_host_is_allowed('', $list));
	rt_assert_false(rwsols_host_is_allowed(':80', $list));
});

rt_test('host: bare IP rejected unless listed', function () use ($list) {
	rt_assert_false(rwsols_host_is_allowed('192.168.1.10', $list));
	rt_assert_false(rwsols_host_is_allowed('192.168.1.10:80', $list));
	$with_ip = array('wol.example.com', '192.168.1.10');
	rt_assert_true(rwsols_host_is_allowed('192.168.1.10', $with_ip));
	rt_assert_true(rwsols_host_is_allowed('192.168.1.10:80', $with_ip));
	rt_assert_false(rwsols_host_is_allowed('192.168.1.11', $with_ip));
});

rt_test('host: [::1]:8080 with and without ALLOW_LOCALHOST', function () use ($list) {
	rt_assert_false(rwsols_host_is_allowed('[::1]:8080', $list, false));
	rt_assert_true(rwsols_host_is_allowed('[::1]:8080', $list, true));
	rt_assert_true(rwsols_host_is_allowed('[::1]', $list, true));
	rt_assert_false(rwsols_host_is_allowed('[::2]:8080', $list, true));
	rt_assert_false(rwsols_host_is_allowed('[::1', $list, true));
	rt_assert_false(rwsols_host_is_allowed('[::1]x', $list, true));
});

rt_test('host: localhost needs ALLOW_LOCALHOST', function () use ($list) {
	rt_assert_false(rwsols_host_is_allowed('localhost', $list, false));
	rt_assert_false(rwsols_host_is_allowed('127.0.0.1:80', $list, false));
	rt_assert_true(rwsols_host_is_allowed('localhost', $list, true));
	rt_assert_true(rwsols_host_is_allowed('LOCALHOST:8080', $list, true));
	rt_assert_true(rwsols_host_is_allowed('127.0.0.1:8080', $list, true));
});

rt_test('host: empty list is legacy (allow everything)', function () {
	rt_assert_true(rwsols_host_is_allowed('anything.example.net', array()));
	rt_assert_true(rwsols_host_is_allowed(null, array()));
	rt_assert_true(rwsols_host_is_allowed("bad\nhost", array()));
	rt_assert_true(rwsols_host_is_allowed('anything', null));
	// ALLOW_LOCALHOST alone does not turn the allowlist on
	rt_assert_true(rwsols_host_is_allowed('anything.example.net', array(), true));
});

rt_test('host: no wildcard / suffix / prefix matching', function () use ($list) {
	rt_assert_false(rwsols_host_is_allowed('evil-wol.example.com', $list));
	rt_assert_false(rwsols_host_is_allowed('wol.example.com.evil.test', $list));
	rt_assert_false(rwsols_host_is_allowed('sub.wol.example.com', $list));
	rt_assert_false(rwsols_host_is_allowed('example.com', $list));
	$wild = array('*.example.com');
	rt_assert_false(rwsols_host_is_allowed('wol.example.com', $wild));
	rt_assert_false(rwsols_host_is_allowed('*.example.com', $wild));
	rt_assert_false(rwsols_host_is_allowed('example.com', $wild));
});

rt_test('host: control chars and whitespace rejected', function () use ($list) {
	foreach (array("wol.example.com\n", "wol.example.com\r\n", " wol.example.com", "wol.example.com ",
		"wol.example.com\0", "wol.example.com\t", "wol.example.com:80\n", "wol.exa mple.com",
		"wol.example.com:80:80", "user@wol.example.com", "wol.example.com/x") as $h) {
		rt_assert_false(rwsols_host_is_allowed($h, $list), 'should reject ' . json_encode($h));
	}
});

rt_test('host: log sanitization strips control chars and caps length', function () {
	$lines = array();
	$opts = array('logger' => function ($l) use (&$lines) { $lines[] = $l; },
		'clock' => new ThClock(), 'state_file' => th_tmpdir() . '/rl.json');
	rwsols_log_host_rejection("203.0.113.5", "evil\r\nInjected: \"x\"\x1b[31m" . str_repeat('a', 500), $opts);
	rt_assert_same(1, count($lines));
	rt_assert_same(0, preg_match('/[^\x20-\x7e]/', $lines[0]), 'no control chars in log line');
	rt_assert_true(strpos($lines[0], '203.0.113.5') !== false);
	rt_assert_true(strlen($lines[0]) < 260, 'log line capped');
	rt_assert_same('(none)', rwsols_sanitize_log_value(null));
});

rt_test('host: rejection log rate limiting with suppressed summary', function () {
	$lines = array();
	$clock = new ThClock();
	$opts = array('logger' => function ($l) use (&$lines) { $lines[] = $l; },
		'clock' => $clock, 'state_file' => th_tmpdir() . '/rl.json');
	for ($i = 0; $i < 25; $i++) {
		rwsols_log_host_rejection('198.51.100.1', 'h' . $i . '.example.net', $opts);
	}
	rt_assert_same(10, count($lines), 'max 10 lines per window');
	$clock->now += 61;
	rwsols_log_host_rejection('198.51.100.1', 'late.example.net', $opts);
	rt_assert_same(12, count($lines));
	rt_assert_true(strpos($lines[10], '15 more') !== false, 'suppressed summary: ' . $lines[10]);
	rt_assert_true(strpos($lines[11], 'late.example.net') !== false);
	// no summary when nothing was suppressed
	$clock->now += 61;
	rwsols_log_host_rejection('198.51.100.1', 'later.example.net', $opts);
	rt_assert_same(13, count($lines));
});

rt_test('host: unwritable rate-limit state never warns or throws', function () {
	$lines = array();
	$opts = array('logger' => function ($l) use (&$lines) { $lines[] = $l; },
		'clock' => new ThClock(), 'state_file' => th_tmpdir() . '/no/such/dir/rl.json');
	for ($i = 0; $i < 12; $i++) {
		rwsols_log_host_rejection('198.51.100.1', 'x.example.net', $opts);
	}
	rt_assert_same(10, count($lines), 'in-process fallback still rate limits');
	$opts['state_file'] = '';
	rwsols_log_host_rejection('198.51.100.1', 'x.example.net', $opts);
});
