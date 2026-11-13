import unittest

from fakes import API_TOKEN, Env, FakeRunner

from rwsols_setup import wizard

DDCLIENT = '/etc/ddclient.conf'
HOST = 'rwsols.example.com'
DDNS_TOKEN = 'DDNSONLYTOKEN-0123456789abcdef'
OFFER = 'Check whether the dynamic DNS token can be used?'
PASTE = (wizard.RECHECK_QUERY, 2)


def conf(token=API_TOKEN, protocol='cloudflare', login='token', extra=''):
    return ('# Configuration file for ddclient\n#password=COMMENTEDOUT-0123456789abcdef\n'
            'protocol=%s\nuse=web\nzone=example.com\nlogin=%s\npassword=%s\n%shome.example.com\n'
            % (protocol, login, token, extra)).encode()


def runner_with(data):
    r = FakeRunner()
    r.files[DDCLIENT] = (data, None)
    return r


class ParseTests(unittest.TestCase):
    def token(self, data):
        return wizard.ddclient_cloudflare_token(runner_with(data))

    def test_finds_cloudflare_api_token(self):
        self.assertEqual(self.token(conf()), API_TOKEN)

    def test_quoted_and_single_line_forms(self):
        self.assertEqual(self.token(conf("'%s'" % API_TOKEN)), API_TOKEN)
        one_line = ('protocol=cloudflare, \\\nzone=example.com, \\\nlogin=token, \\\npassword=%s \\\nhome.example.com\n' % API_TOKEN).encode()
        self.assertEqual(self.token(one_line), API_TOKEN)

    def test_other_providers_and_global_api_key_are_never_used(self):
        self.assertIsNone(self.token(conf(protocol='dyndns2')))
        self.assertIsNone(self.token(conf(login='someone@example.com')))

    def test_ambiguous_or_missing_files_give_nothing(self):
        self.assertIsNone(wizard.ddclient_cloudflare_token(FakeRunner()))
        self.assertIsNone(self.token(b''))
        self.assertIsNone(self.token(conf(extra='protocol=dyndns2\nlogin=u\npassword=otherpassword-0123456789\nother.example.net\n')))
        self.assertIsNone(self.token(conf(extra='password=SECONDTOKEN-0123456789abcdef\n')))
        self.assertIsNone(self.token(conf(token='short')))


class ReuseTests(unittest.TestCase):
    def run_wizard(self, data, rules=(), secrets=(), token_env=False, access=False, on_recheck=None):
        rules = [('Public hostname', HOST)] + list(rules)
        if access:
            rules += [('Enable Cloudflare Access', True), ('Emails allowed', 'me@example.com')]
        env = Env(rules, runner=runner_with(data), token=token_env, secrets=list(secrets))
        self.addCleanup(env.cleanup)
        env.cf.limited_tokens[DDNS_TOKEN] = ('cfd_tunnel', '/access/')
        if on_recheck:
            choose = env.ui.choose

            def choosing(query, options, default=None):
                if query == wizard.RECHECK_QUERY:
                    on_recheck(env)
                return choose(query, options, default)
            env.ui.choose = choosing
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        w.prepare_cloud()
        return env, w

    def test_sufficient_token_is_used_and_nothing_is_asked(self):
        env, w = self.run_wizard(conf())
        text = env.ui.text()
        self.assertIn(OFFER, text)
        self.assertIn('Using it; nothing to paste.', text)
        self.assertNotIn('Cloudflare API token', [l for l in env.ui.out if l == 'Cloudflare API token'])
        self.assertTrue(env.cf.tunnels)
        self.assertEqual(set(env.cf.tokens_used), {API_TOKEN})

    def test_dns_only_token_reports_the_gap_and_falls_back_to_the_prompt(self):
        env, w = self.run_wizard(conf(DDNS_TOKEN), rules=[PASTE], secrets=[API_TOKEN])
        text = env.ui.text()
        self.assertIn('The dynamic DNS token cannot be used as it is:', text)
        self.assertIn('it lacks the permission Account > Cloudflare Tunnel > Edit.', text)
        self.assertNotIn('Access: Apps and Policies', text.split('cannot be used as it is:')[1].split('To reuse this token')[0])
        self.assertIn('Cloudflare API token', env.ui.out)
        self.assertTrue(env.cf.tunnels)
        mutating = [t for t, c in zip(env.cf.tokens_used, env.cf.calls) if c[0] != 'GET']
        self.assertEqual(set(mutating), {API_TOKEN})         # the weak token never changed anything

    def test_access_permission_is_checked_only_when_access_is_wanted(self):
        env, w = self.run_wizard(conf(DDNS_TOKEN), rules=[PASTE], secrets=[API_TOKEN], access=True)
        gap = env.ui.text().split('cannot be used as it is:')[1].split('To reuse this token')[0]
        self.assertIn('Account > Access: Apps and Policies > Edit', gap)

    def test_check_again_after_adding_the_permission_uses_the_token(self):
        # The user fixes the token in the Cloudflare dashboard while the wizard waits, then picks "check again".
        env, w = self.run_wizard(conf(DDNS_TOKEN), on_recheck=lambda env: env.cf.limited_tokens.update({DDNS_TOKEN: ()}))
        text = env.ui.text()
        self.assertIn('The dynamic DNS token cannot be used as it is:', text)
        self.assertIn('Checking the token again...', text)
        self.assertIn('Using it; nothing to paste.', text)
        self.assertNotIn('Cloudflare API token', env.ui.out)       # never asked to paste
        self.assertTrue(env.cf.tunnels)
        self.assertEqual(set(env.cf.tokens_used), {DDNS_TOKEN})

    def test_check_again_without_fixing_asks_again_and_eventually_falls_back(self):
        env, w = self.run_wizard(conf(DDNS_TOKEN), secrets=[API_TOKEN])   # default answer: check again, every time
        text = env.ui.text()
        self.assertEqual(env.ui.out.count(wizard.RECHECK_QUERY), wizard.MAX_TOKEN_RECHECKS)
        self.assertIn('Still not usable', text)
        self.assertIn('Cloudflare API token', env.ui.out)
        self.assertTrue(env.cf.tunnels)

    def test_ddns_step_note_names_the_tunnel_permission_and_the_later_chance(self):
        note = ' '.join(wizard.DDNS_TOKEN_NOTE)
        self.assertIn('Account > Cloudflare Tunnel > Edit', note)
        self.assertIn('optional', note)
        self.assertIn('lets you add the permission then', note)

    def test_declining_goes_straight_to_the_prompt(self):
        env, w = self.run_wizard(conf(), rules=[(OFFER, False)], secrets=[API_TOKEN])
        self.assertIn('Cloudflare API token', env.ui.out)
        self.assertNotIn('Using it; nothing to paste.', env.ui.text())

    def test_environment_token_takes_priority_and_no_offer_is_made(self):
        env, w = self.run_wizard(conf(DDNS_TOKEN), token_env=True)
        self.assertNotIn(OFFER, env.ui.text())
        self.assertTrue(env.cf.tunnels)

    def test_no_offer_for_other_ddns_providers(self):
        env, w = self.run_wizard(conf(protocol='dyndns2'), secrets=[API_TOKEN])
        self.assertNotIn(OFFER, env.ui.text())

    def test_token_never_appears_in_output_state_or_config(self):
        env, w = self.run_wizard(conf(DDNS_TOKEN), rules=[PASTE], secrets=[API_TOKEN])
        for blob in (env.ui.text(), env.state_text(), env.config()):
            self.assertNotIn(DDNS_TOKEN, blob)
            self.assertNotIn(API_TOKEN, blob)
        for call in env.runner.calls:
            self.assertNotIn(DDNS_TOKEN, ' '.join(call['argv']))


if __name__ == '__main__':
    unittest.main()
