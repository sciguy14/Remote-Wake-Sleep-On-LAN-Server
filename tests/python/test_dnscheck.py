import json
import os
import unittest

from fakes import Env, FakeCloudflare, FakeRunner

from rwsols_setup import dnscheck, wizard
from rwsols_setup.runtime import HttpResponse

DDCLIENT = '/etc/ddclient.conf'
SKIP = 'Skipping the Cloudflare Tunnel and Cloudflare Access options.'


def ddclient(*names):
    return ('protocol=dyndns2\nlogin=u\npassword=p\n%s\n' % ', '.join(names)).encode(), None


class CheckDomainTests(unittest.TestCase):
    def setUp(self):
        self.cf = FakeCloudflare()

    def test_apex_on_cloudflare(self):
        c = dnscheck.check_domain(self.cf, 'Example.COM.')
        self.assertEqual((c.status, c.zone), ('cloudflare', 'example.com'))
        self.assertEqual(c.nameservers, ['ada.ns.cloudflare.com', 'bob.ns.cloudflare.com'])

    def test_subdomain_and_not_yet_existing_name_resolve_to_their_zone(self):
        c = dnscheck.check_domain(self.cf, 'rwsols.home.example.com')
        self.assertEqual((c.status, c.zone), ('cloudflare', 'example.com'))
        self.assertEqual(self.cf.doh_calls, ['rwsols.home.example.com', 'example.com'])

    def test_other_dns_host(self):
        c = dnscheck.check_domain(self.cf, 'pi.elsewhere.example.net')
        self.assertEqual((c.status, c.zone), ('other', 'elsewhere.example.net'))
        self.assertIn('is not managed by Cloudflare (its nameservers are ns1.dns-host.example', dnscheck.describe(c))

    def test_lookalike_nameserver_is_not_cloudflare(self):
        self.cf.ns['example.org'] = ['ns.cloudflare.com.evil.example']
        self.assertEqual(dnscheck.check_domain(self.cf, 'example.org').status, 'other')

    def test_unregistered_domain(self):
        c = dnscheck.check_domain(self.cf, 'nothing-here.example.org')
        self.assertEqual(c.status, 'missing')
        self.assertIn('does not appear to be a registered domain', dnscheck.describe(c))

    def test_failed_lookup_is_unknown_and_never_raises(self):
        self.cf.ns = None
        self.assertEqual(dnscheck.check_domain(self.cf, 'example.com').status, 'unknown')

        class Garbage(object):
            def request(self, *a, **kw):
                return HttpResponse(200, b'<html>captive portal</html>', {})

        class Down(object):
            def request(self, *a, **kw):
                from rwsols_setup.errors import NetworkError
                raise NetworkError('no route')
        self.assertEqual(dnscheck.check_domain(Garbage(), 'example.com').status, 'unknown')
        self.assertEqual(dnscheck.check_domain(Down(), 'example.com').status, 'unknown')

    def test_sends_only_the_name_and_no_credentials(self):
        seen = []

        class Spy(FakeCloudflare):
            def request(self, method, url, headers=None, **kw):
                seen.append((method, url, dict(headers or {})))
                return FakeCloudflare.request(self, method, url, headers=headers, **kw)
        dnscheck.check_domain(Spy(), 'example.com')
        self.assertEqual(seen, [('GET', dnscheck.DOH_URL + '?name=example.com&type=NS', {'Accept': 'application/dns-json'})])


class ModeGateTests(unittest.TestCase):
    def env(self, rules=None, names=None, **kw):
        runner = FakeRunner()
        if names:
            runner.files[DDCLIENT] = ddclient(*names)
        env = Env(rules, runner=runner, **kw)
        self.addCleanup(env.cleanup)
        self.offered = []
        real = env.ui.choose

        def choose(query, options, default=None):
            self.offered.append(list(options))
            return real(query, options, default)
        env.ui.choose = choose
        return env

    def labels(self):
        return self.offered[-1]

    def test_ddclient_domain_on_cloudflare_offers_tunnel_without_asking(self):
        env = self.env(names=['home.example.com'])
        wizard.choose_mode(env.ctx)
        self.assertEqual(len(self.labels()), 3)
        text = env.ui.text()
        self.assertIn('DNS for example.com is managed by Cloudflare. Cloudflare Tunnel is available.', text)
        self.assertNotIn('Domain to use with Cloudflare', text)
        self.assertNotIn(SKIP, text)
        self.assertEqual(env.ctx.cloudflare_base, 'home.example.com')

    def test_explains_that_the_ddns_method_does_not_matter(self):
        env = self.env(names=['home.example.com'])
        wizard.choose_mode(env.ctx)
        text = env.ui.text()
        self.assertIn('only work with a domain whose DNS is managed by Cloudflare', text)
        self.assertIn('the dynamic DNS feature of your router', text)

    def test_ddclient_domain_elsewhere_and_no_other_domain_hides_tunnel_and_says_why(self):
        env = self.env(names=['pi.elsewhere.example.net'])
        self.assertEqual(wizard.choose_mode(env.ctx), 'port_forward')
        self.assertEqual(self.labels(), [wizard.MODE_LABELS['none'], wizard.MODE_LABELS['port_forward']])
        text = env.ui.text()
        self.assertIn(SKIP + ' DNS for elsewhere.example.net is not managed by Cloudflare', text)
        self.assertIn('python3 setup.py --remote-access', text)

    def test_user_can_name_a_cloudflare_domain_updated_some_other_way(self):
        env = self.env([('Domain to use with Cloudflare', 'example.com'), ('Choose a remote access mode', 3)],
                       names=['pi.elsewhere.example.net'])
        self.assertEqual(wizard.choose_mode(env.ctx), 'tunnel')
        self.assertEqual(env.ctx.cloudflare_base, 'example.com')
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w._plan_tunnel()
        self.assertEqual(w.new['TUNNEL_HOSTNAME'], 'rwsols.example.com')

    def test_wrong_domain_is_reasked_until_blank(self):
        env = self.env([('Domain to use with Cloudflare', ['elsewhere.example.net', 'nothing-here.example.org', ''])])
        os.remove(env.config_path)
        self.assertEqual(wizard.choose_mode(env.ctx), 'none')
        self.assertEqual(len(self.labels()), 2)
        text = env.ui.text()
        self.assertIn('nothing-here.example.org does not appear to be a registered domain', text)
        self.assertIn(SKIP, text)

    def test_no_domain_at_all(self):
        env = self.env()
        wizard.choose_mode(env.ctx)
        self.assertEqual(len(self.labels()), 2)
        self.assertIn(SKIP + ' You did not name a domain whose DNS is managed by Cloudflare.', env.ui.text())

    def test_failed_lookup_keeps_the_option(self):
        env = self.env(names=['home.example.com'])
        env.cf.ns = None
        wizard.choose_mode(env.ctx)
        self.assertEqual(len(self.labels()), 3)
        text = env.ui.text()
        self.assertIn('Could not check who manages DNS for home.example.com', text)
        self.assertIn('shown anyway', text)
        self.assertNotIn(SKIP, text)

    def test_existing_tunnel_install_is_never_stranded(self):
        env = self.env(names=['pi.elsewhere.example.net'])
        with open(env.ctx.state_path, 'w') as f:
            json.dump({'mode': 'tunnel', 'tunnel': {'hostname': 'rwsols.elsewhere.example.net'}}, f)
        self.assertEqual(wizard.choose_mode(env.ctx), 'tunnel')
        self.assertEqual(len(self.labels()), 3)

    def test_tunnel_hostname_must_be_in_a_cloudflare_zone(self):
        env = self.env([('Public hostname', ['rwsols.elsewhere.example.net', 'rwsols.example.com'])])
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w._plan_tunnel()
        self.assertEqual(w.new['TUNNEL_HOSTNAME'], 'rwsols.example.com')
        self.assertIn('Use a name in a domain that is on Cloudflare.', env.ui.text())

    def test_tunnel_hostname_accepted_when_lookup_fails(self):
        env = self.env([('Public hostname', 'rwsols.example.com')])
        env.cf.ns = None
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w._plan_tunnel()
        self.assertEqual(w.new['TUNNEL_HOSTNAME'], 'rwsols.example.com')


if __name__ == '__main__':
    unittest.main()
