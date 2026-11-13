import json
import unittest
from urllib.parse import parse_qs, urlsplit

from fakes import API_TOKEN, Env

from rwsols_setup import lancert
from rwsols_setup.errors import SetupError

NAME = 'pi.home.example.com'
DNS_TOKEN = 'DNSONLYTOKEN-0123456789abcdef'
RULES = [('LAN name for the certificate', NAME), ('Email address', 'me@example.com')]


def make(rules=RULES, secrets=()):
    env = Env(list(rules), secrets=list(secrets), token=False)
    env.cf.limited_tokens[DNS_TOKEN] = ('cfd_tunnel', '/access/')       # a DNS-only token
    return env


class NameTests(unittest.TestCase):
    def test_only_real_domain_names_qualify(self):
        self.assertEqual(lancert.lan_name('Pi.Home.Example.com'), NAME)
        for bad in ('raspberrypi.local', '192.168.1.10', 'raspberrypi', ''):
            with self.assertRaises(ValueError):
                lancert.lan_name(bad)


class TokenLinkTests(unittest.TestCase):
    def test_link_asks_for_dns_permissions_only(self):
        url = lancert.token_template_url(NAME)
        q = parse_qs(urlsplit(url).query)
        keys = [(p['key'], p['type']) for p in json.loads(q['permissionGroupKeys'][0])]
        self.assertEqual(keys, [('dns', 'edit'), ('zone', 'read')])
        self.assertEqual(q['name'], ['RWSOLS LAN certificate (%s)' % NAME])


class PlanTests(unittest.TestCase):
    def test_reuses_the_dynamic_dns_token_when_chosen(self):
        env = make()
        self.addCleanup(env.cleanup)
        names, email, token, cert_names = lancert.plan(env.ctx, ['raspberrypi.local', NAME], DNS_TOKEN)
        self.assertEqual((names, email, token), ([NAME], 'me@example.com', DNS_TOKEN))
        self.assertEqual(cert_names, [NAME])                                  # one name is the default
        self.assertIn(lancert.COVER_QUERY, env.ui.text())
        text = env.ui.text()
        self.assertIn('can never get a trusted certificate', text)
        self.assertIn('stays on this Pi', text)
        self.assertNotIn('Cloudflare API token (DNS only)', env.ui.out)       # nothing to paste
        self.assertNotIn(DNS_TOKEN, text)
        self.assertEqual(set(c[0] for c in env.cf.calls), {'GET'})

    def test_pasted_token_is_checked_and_a_bad_one_is_refused(self):
        env = make(RULES + [(lancert.SOURCE_QUERY, 2)], secrets=['NOTATOKEN-0123456789abcdef', DNS_TOKEN])
        self.addCleanup(env.cleanup)
        env.cf.limited_tokens['NOTATOKEN-0123456789abcdef'] = ('/zones',)
        names, email, token, _ = lancert.plan(env.ctx, [], DNS_TOKEN)
        self.assertEqual(token, DNS_TOKEN)
        self.assertIn('That token cannot see the Cloudflare zone', env.ui.text())
        self.assertTrue([l for l in env.ui.out if l.startswith(lancert.TOKEN_PAGE + '?')])

    def test_no_dynamic_dns_token_goes_straight_to_the_paste_prompt(self):
        env = make(secrets=[DNS_TOKEN])
        self.addCleanup(env.cleanup)
        lancert.plan(env.ctx, [], None)
        self.assertNotIn(lancert.SOURCE_QUERY, env.ui.out)
        self.assertIn('Cloudflare API token (DNS only)', env.ui.out)

    def test_name_outside_cloudflare_is_refused(self):
        env = make([('LAN name for the certificate', ['pi.other.example.net', NAME]), ('Email address', 'me@example.com')],
                   secrets=[DNS_TOKEN])
        self.addCleanup(env.cleanup)
        env.cf.ns['example.net'] = ['ns1.registrar.example.']
        names, _, _, _ = lancert.plan(env.ctx, [], None)
        self.assertEqual(names, [NAME])

    def test_wildcard_covers_every_name_next_to_the_pi_and_says_what_that_means(self):
        env = make(RULES + [(lancert.COVER_QUERY, 2)])
        self.addCleanup(env.cleanup)
        names, _, _, cert_names = lancert.plan(env.ctx, [], DNS_TOKEN)
        self.assertEqual((names, cert_names), ([NAME], ['*.home.example.com']))   # the wildcard already includes the Pi's name
        text = env.ui.text()
        for part in ('nas.home.example.com', 'Not covered: home.example.com itself', 'copy both files',
                     'This script does neither for you.', 'can pose as any name under home.example.com'):
            self.assertIn(part, text)

    def test_wildcard_stays_inside_the_zone(self):
        self.assertEqual(lancert.wildcard_for('pi.home.example.com', 'example.com'), '*.home.example.com')
        self.assertEqual(lancert.wildcard_for('pi.example.com', 'example.com'), '*.example.com')
        for name, zone in (('example.com', 'example.com'), ('pi.home.example.com', None), ('pi.example.com', 'pi.example.com')):
            self.assertIsNone(lancert.wildcard_for(name, zone))


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.env = make()
        self.addCleanup(self.env.cleanup)
        self.r = self.env.runner

    def argvs(self):
        return [' '.join(c['argv']) for c in self.r.calls]

    def test_issue_stores_the_token_root_only_and_never_on_a_command_line(self):
        lancert.issue(self.env.ctx, [NAME], 'me@example.com', DNS_TOKEN)
        data, mode = self.r.files[lancert.CREDENTIALS_PATH]
        self.assertEqual((data, mode), (('dns_cloudflare_api_token = %s\n' % DNS_TOKEN).encode(), '0600'))
        for call in self.r.calls:
            if call['argv'][0] != '<write_file>':
                self.assertNotIn(DNS_TOKEN, ' '.join(call['argv']) + str(call['input'] or ''))
        self.assertNotIn(DNS_TOKEN, self.env.ui.text() + self.env.state_text())
        certbot = [a for a in self.argvs() if a.startswith('certbot certonly')][0]
        for part in ('--dns-cloudflare-credentials ' + lancert.CREDENTIALS_PATH, '--cert-name rwsols-lan', '-d ' + NAME,
                     '--renew-with-new-domains',
                     '--non-interactive', '--agree-tos', '--email me@example.com', '--deploy-hook systemctl reload apache2'):
            self.assertIn(part, certbot)
        self.assertIn('ln -sfn /etc/letsencrypt/live/rwsols-lan/fullchain.pem /etc/apache2/ssl/wol.crt', self.argvs())
        self.assertIn('ln -sfn /etc/letsencrypt/live/rwsols-lan/privkey.pem /etc/apache2/ssl/wol.key', self.argvs())

    def test_plugin_follows_how_certbot_is_installed(self):
        lancert.install_plugin(self.env.ctx)
        self.assertIn('snap install certbot-dns-cloudflare', self.argvs())
        self.r.calls[:] = []
        self.r.rc[('snap', 'list')] = 1
        lancert.install_plugin(self.env.ctx)
        self.assertIn('apt-get install -y certbot python3-certbot-dns-cloudflare', self.argvs())

    def test_certbot_failure_changes_nothing_in_apache(self):
        self.r.rc[('certbot', 'certonly')] = 1
        with self.assertRaises(SetupError):
            lancert.issue(self.env.ctx, [NAME], 'me@example.com', DNS_TOKEN)
        self.assertFalse([a for a in self.argvs() if a.startswith('ln ')])

    def test_cleanup_removes_the_certificate_and_the_stored_token(self):
        self.assertFalse(lancert.cleanup(self.env.ctx))                     # nothing there: nothing done
        self.assertEqual(self.r.mutations, [])
        lancert.issue(self.env.ctx, [NAME], 'me@example.com', DNS_TOKEN)
        self.assertTrue(lancert.cleanup(self.env.ctx))
        self.assertNotIn(lancert.CREDENTIALS_PATH, self.r.files)
        self.assertIn('certbot delete --non-interactive --cert-name rwsols-lan', self.argvs())

    def test_token_used_for_api_checks_is_the_only_one_sent(self):
        self.assertTrue(lancert.zone_visible(self.env.ctx, DNS_TOKEN, NAME))
        self.assertEqual(set(self.env.cf.tokens_used), {DNS_TOKEN})
        self.assertNotIn(API_TOKEN, self.env.cf.tokens_used)


if __name__ == '__main__':
    unittest.main()
