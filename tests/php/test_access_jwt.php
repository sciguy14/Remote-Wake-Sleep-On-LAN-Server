<?php

function tj_verify($token, $fetcher = null, $clock = null, $dir = null)
{
	if ($fetcher === null) {
		$fetcher = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	}
	if ($clock === null) {
		$clock = new ThClock();
	}
	return rwsols_access_verify($token, TH_TEAM, TH_AUD, th_opts($fetcher, $clock, $dir));
}

function tj_assert_rejected($result, $why = '')
{
	rt_assert_false($result['ok'], 'token should be rejected' . ($why !== '' ? " ($why)" : '') . '; error=' . $result['error']);
	rt_assert_same(null, $result['claims']);
}

rt_test('jwt: valid token', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$r = tj_verify(th_token(th_claims()), $f);
	rt_assert_true($r['ok'], 'error=' . $r['error']);
	rt_assert_same('user@example.com', rwsols_access_identity($r['claims']));
	rt_assert_same(1, $f->calls);
	rt_assert_same('https://' . TH_TEAM . '/cdn-cgi/access/certs', $f->urls[0]);
});

rt_test('jwt: aud as string and as array', function () {
	rt_assert_true(tj_verify(th_token(th_claims(array('aud' => TH_AUD))))['ok']);
	rt_assert_true(tj_verify(th_token(th_claims(array('aud' => array('other-aud', TH_AUD)))))['ok']);
});

rt_test('jwt: wrong aud rejected', function () {
	tj_assert_rejected(tj_verify(th_token(th_claims(array('aud' => 'other-aud')))));
	tj_assert_rejected(tj_verify(th_token(th_claims(array('aud' => array('other-aud'))))));
	tj_assert_rejected(tj_verify(th_token(th_claims(array('aud' => array())))));
	$c = th_claims();
	unset($c['aud']);
	tj_assert_rejected(tj_verify(th_token($c)), 'missing aud');
});

rt_test('jwt: wrong iss rejected', function () {
	tj_assert_rejected(tj_verify(th_token(th_claims(array('iss' => 'https://other.cloudflareaccess.com')))));
	tj_assert_rejected(tj_verify(th_token(th_claims(array('iss' => TH_TEAM)))));
	$c = th_claims();
	unset($c['iss']);
	tj_assert_rejected(tj_verify(th_token($c)), 'missing iss');
});

rt_test('jwt: expired rejected, expired within leeway accepted', function () {
	tj_assert_rejected(tj_verify(th_token(th_claims(array('exp' => TH_NOW - 61)))));
	rt_assert_true(tj_verify(th_token(th_claims(array('exp' => TH_NOW - 30))))['ok']);
});

rt_test('jwt: missing or non-numeric exp rejected', function () {
	$c = th_claims();
	unset($c['exp']);
	tj_assert_rejected(tj_verify(th_token($c)));
	tj_assert_rejected(tj_verify(th_token(th_claims(array('exp' => (string) (TH_NOW + 3600))))));
});

rt_test('jwt: nbf in future rejected (leeway honored)', function () {
	tj_assert_rejected(tj_verify(th_token(th_claims(array('nbf' => TH_NOW + 120)))));
	rt_assert_true(tj_verify(th_token(th_claims(array('nbf' => TH_NOW + 30))))['ok']);
});

rt_test('jwt: bad signature rejected', function () {
	// signed with key b but claims kid-a
	tj_assert_rejected(tj_verify(th_token(th_claims(), 'b', 'kid-a')));
	$t = th_token(th_claims());
	$parts = explode('.', $t);
	$sig = rwsols_b64url_decode($parts[2]);
	$sig[10] = chr(ord($sig[10]) ^ 1);
	tj_assert_rejected(tj_verify($parts[0] . '.' . $parts[1] . '.' . th_b64url($sig)));
});

rt_test('jwt: tampered payload rejected', function () {
	$parts = explode('.', th_token(th_claims()));
	$evil = th_b64url(json_encode(th_claims(array('email' => 'admin@example.com'))));
	tj_assert_rejected(tj_verify($parts[0] . '.' . $evil . '.' . $parts[2]));
});

rt_test('jwt: alg none rejected', function () {
	$h = th_b64url(json_encode(array('alg' => 'none', 'kid' => 'kid-a', 'typ' => 'JWT')));
	$p = th_b64url(json_encode(th_claims()));
	tj_assert_rejected(tj_verify($h . '.' . $p . '.'));
	tj_assert_rejected(tj_verify($h . '.' . $p . '.' . th_b64url('x')));
	$parts = explode('.', th_token(th_claims(), 'a', 'kid-a', array('alg' => 'NONE')));
	tj_assert_rejected(tj_verify(implode('.', $parts)));
	tj_assert_rejected(tj_verify(th_token(th_claims(), 'a', 'kid-a', array('alg' => 'RS512'))));
	tj_assert_rejected(tj_verify(th_token(th_claims(), 'a', 'kid-a', array('alg' => 'rs256'))));
});

rt_test('jwt: HS256 signed with public key as HMAC secret rejected', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$c = new ThClock();
	$dir = th_tmpdir();
	rt_assert_true(tj_verify(th_token(th_claims()), $f, $c, $dir)['ok']);
	$pem = rwsols_access_get_key(TH_TEAM, 'kid-a', th_opts($f, $c, $dir));
	rt_assert_true(is_string($pem));
	tj_assert_rejected(tj_verify(th_hs256_token(th_claims(), $pem), $f, $c, $dir));
	tj_assert_rejected(tj_verify(th_hs256_token(th_claims(), th_key('a')['pub_pem']), $f, $c, $dir));
});

rt_test('jwt: unknown kid triggers exactly one refetch, then succeeds', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$c = new ThClock();
	$dir = th_tmpdir();
	rt_assert_true(tj_verify(th_token(th_claims()), $f, $c, $dir)['ok']);
	rt_assert_same(1, $f->calls);
	$c->now += 120; // outside the forced-refresh window, inside the cache TTL
	$f->body = th_jwks(array('kid-a' => 'a', 'kid-b' => 'b'));
	$r = tj_verify(th_token(th_claims(array('exp' => $c->now + 3600)), 'b', 'kid-b'), $f, $c, $dir);
	rt_assert_true($r['ok'], 'error=' . $r['error']);
	rt_assert_same(2, $f->calls);
	// both keys now cached
	rt_assert_true(tj_verify(th_token(th_claims(array('exp' => $c->now + 3600)), 'b', 'kid-b'), $f, $c, $dir)['ok']);
	rt_assert_true(tj_verify(th_token(th_claims(array('exp' => $c->now + 3600))), $f, $c, $dir)['ok']);
	rt_assert_same(2, $f->calls);
});

rt_test('jwt: unknown kid still unknown -> reject; no refetch inside rate-limit window', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$c = new ThClock();
	$dir = th_tmpdir();
	rt_assert_true(tj_verify(th_token(th_claims()), $f, $c, $dir)['ok']);
	$c->now += 120;
	$tok = th_token(th_claims(array('exp' => $c->now + 3600)), 'b', 'kid-zzz');
	tj_assert_rejected(tj_verify($tok, $f, $c, $dir));
	rt_assert_same(2, $f->calls, 'one forced refetch');
	$c->now += 10;
	tj_assert_rejected(tj_verify($tok, $f, $c, $dir));
	tj_assert_rejected(tj_verify(th_token(th_claims(array('exp' => $c->now + 3600)), 'b', 'kid-other'), $f, $c, $dir));
	rt_assert_same(2, $f->calls, 'no refetch inside window');
	// known key still works while rate limited
	rt_assert_true(tj_verify(th_token(th_claims(array('exp' => $c->now + 3600))), $f, $c, $dir)['ok']);
	$c->now += 61;
	tj_assert_rejected(tj_verify($tok, $f, $c, $dir));
	rt_assert_same(3, $f->calls, 'refetch allowed after window');
});

rt_test('jwt: fetch failure with empty cache fails closed', function () {
	$f = new ThFetcher(false);
	$c = new ThClock();
	$dir = th_tmpdir();
	tj_assert_rejected(tj_verify(th_token(th_claims()), $f, $c, $dir));
	rt_assert_same(1, $f->calls);
	tj_assert_rejected(tj_verify(th_token(th_claims()), $f, $c, $dir));
	rt_assert_same(1, $f->calls, 'failed fetches are rate limited too');
	$f->body = '{"keys": "garbage"}';
	$c->now += 61;
	tj_assert_rejected(tj_verify(th_token(th_claims()), $f, $c, $dir));
	$throwing = function ($url) { throw new RuntimeException('network down'); };
	tj_assert_rejected(tj_verify(th_token(th_claims()), $throwing, $c, th_tmpdir()));
});

rt_test('jwt: cached keys used without fetching (also across a fresh process state)', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$c = new ThClock();
	$dir = th_tmpdir();
	rt_assert_true(tj_verify(th_token(th_claims()), $f, $c, $dir)['ok']);
	$f2 = new ThFetcher(false);
	$c->now += 1800;
	rt_assert_true(tj_verify(th_token(th_claims()), $f2, $c, $dir)['ok']);
	rt_assert_same(0, $f2->calls, 'served from disk cache');
});

rt_test('jwt: cache expires after TTL; expired cache is not used if refetch fails', function () {
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$c = new ThClock();
	$dir = th_tmpdir();
	rt_assert_true(tj_verify(th_token(th_claims()), $f, $c, $dir)['ok']);
	$c->now += 3601;
	$f->body = false;
	tj_assert_rejected(tj_verify(th_token(th_claims(array('exp' => $c->now + 3600))), $f, $c, $dir));
	rt_assert_same(2, $f->calls);
});

rt_test('jwt: public_certs fallback when kid only appears there', function () {
	$doc = json_decode(th_jwks(array('kid-a' => 'a')), true);
	$certs_only = json_decode(th_jwks(array('kid-b' => 'b'), false, true), true);
	$doc['public_certs'] = $certs_only['public_certs'];
	$f = new ThFetcher(json_encode($doc));
	$c = new ThClock();
	$dir = th_tmpdir();
	rt_assert_true(tj_verify(th_token(th_claims()), $f, $c, $dir)['ok']);
	$r = tj_verify(th_token(th_claims(), 'b', 'kid-b'), $f, $c, $dir);
	rt_assert_true($r['ok'], 'error=' . $r['error']);
});

rt_test('jwt: JWK to PEM matches the real public key', function () {
	$k = th_key('a');
	$pem = rwsols_jwk_to_pem(th_b64url($k['n']), th_b64url($k['e']));
	$d = openssl_pkey_get_details(openssl_pkey_get_public($pem));
	rt_assert_same($k['n'], $d['rsa']['n']);
	rt_assert_same($k['e'], $d['rsa']['e']);
});

rt_test('jwt: malformed tokens rejected', function () {
	$good = th_token(th_claims());
	$parts = explode('.', $good);
	$bad = array(
		'', 'abc', 'a.b', $good . '.x', $parts[0] . '.' . $parts[1],
		$parts[0] . '..' . $parts[2],
		'=' . $good,
		$parts[0] . '=.' . $parts[1] . '.' . $parts[2],
		str_replace('-', '+', $parts[0]) . '+/.' . $parts[1] . '.' . $parts[2],
		th_b64url('[]') . '.' . $parts[1] . '.' . $parts[2],
		$parts[0] . '.' . th_b64url('[1,2]') . '.' . $parts[2],
		$parts[0] . '.' . th_b64url('"str"') . '.' . $parts[2],
		th_b64url('not json') . '.' . $parts[1] . '.' . $parts[2],
		$parts[0] . '.' . $parts[1] . '.' . $parts[2] . str_repeat('A', 9000),
		" " . $good,
		$good . "\n",
	);
	foreach ($bad as $t) {
		tj_assert_rejected(tj_verify($t), json_encode(substr($t, 0, 40)));
	}
	tj_assert_rejected(tj_verify(null));
	tj_assert_rejected(tj_verify(array($good)));
	$c = th_claims();
	tj_assert_rejected(tj_verify(th_token($c, 'a', '')), 'empty kid');
	$nokid = th_b64url(json_encode(array('alg' => 'RS256'))) . '.' . $parts[1] . '.' . $parts[2];
	tj_assert_rejected(tj_verify($nokid), 'no kid');
});

rt_test('jwt: strict base64url decoding', function () {
	rt_assert_same('hi', rwsols_b64url_decode('aGk'));
	rt_assert_false(rwsols_b64url_decode('aGk='));
	rt_assert_false(rwsols_b64url_decode('aGl'), 'non-canonical trailing bits');
	rt_assert_false(rwsols_b64url_decode('a'));
	rt_assert_false(rwsols_b64url_decode('a+b/'));
	rt_assert_false(rwsols_b64url_decode(''));
});

rt_test('jwt: team domain must be a plain hostname', function () {
	rt_assert_true(rwsols_access_valid_team_domain('example.cloudflareaccess.com'));
	foreach (array('https://example.cloudflareaccess.com', 'example.cloudflareaccess.com/', 'example.com:443',
		'user@example.com', 'example', 'example.com/evil?', 'Example.com', "example.com\n", '', 'exa mple.com',
		'-bad.example.com', '127.0.0.1') as $d) {
		rt_assert_false(rwsols_access_valid_team_domain($d), json_encode($d));
	}
	$f = new ThFetcher(th_jwks(array('kid-a' => 'a')));
	$r = rwsols_access_verify(th_token(th_claims()), 'evil.example.com/x', TH_AUD, th_opts($f, new ThClock()));
	tj_assert_rejected($r);
	rt_assert_same(0, $f->calls, 'no fetch for invalid team domain');
});

rt_test('jwt: service token claims yield common_name identity', function () {
	$c = th_claims(array('common_name' => 'svc-0000.access'));
	unset($c['email']);
	$r = tj_verify(th_token($c));
	rt_assert_true($r['ok']);
	rt_assert_same('svc-0000.access', rwsols_access_identity($r['claims']));
	rt_assert_same(null, rwsols_access_identity(array('sub' => 'x')));
});
