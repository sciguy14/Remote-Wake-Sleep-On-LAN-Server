import json
import os
import unittest

from fakes import Env, FakeCloudflare, FakeRunner

from rwsols_setup import dnscheck, wizard
from rwsols_setup.runtime import HttpResponse

DDCLIENT = '/etc/ddclient.conf'
SKIP = wizard.NOT_AVAILABLE
OUTSIDE = (wizard.WHERE_QUERY, 2)


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
        self.assertEqual(self.labels(), wizard.METHOD_OPTIONS)
        text = env.ui.text()
        self.assertLess(text.index(wizard.WHERE_QUERY), text.index('Checking your domain'))
        self.assertLess(text.index('Cloudflare Tunnel is available.'), text.index(wizard.METHOD_QUERY))
        self.assertIn('DNS provider: Cloudflare. Cloudflare Tunnel is available.', text)
        self.assertNotIn('Your domain, e.g. example.com', text)
        self.assertNotIn(SKIP, text)
        self.assertEqual(env.ctx.cloudflare_base, 'home.example.com')
        self.assertEqual(env.ctx.checked_domain, {'name': 'example.com', 'provider': 'cloudflare'})   # kept for the dynamic DNS section

    def test_explains_that_the_ddns_method_does_not_matter(self):
        env = self.env(names=['home.example.com'])
        wizard.choose_mode(env.ctx)
        text = env.ui.text()
        self.assertIn('If Cloudflare is detected as the DNS provider, you will be able to choose between port forwarding and a '
                      'Cloudflare Tunnel configuration.', text)
        self.assertIn('If any other DNS provider is detected, only port forwarding will be available.', text)
        self.assertIn('Found home.example.com (part of the domain example.com).', text)
        self.assertIn('Nameservers: ada.ns.cloudflare.com, bob.ns.cloudflare.com', text)
        self.assertIn('the dynamic DNS feature of your router', text)

    def test_ddclient_domain_elsewhere_and_no_other_domain_hides_tunnel_and_says_why(self):
        env = self.env(names=['pi.elsewhere.example.net'])
        self.assertEqual(wizard.choose_mode(env.ctx), 'port_forward')
        text = env.ui.text()
        self.assertNotIn(wizard.METHOD_QUERY, text)   # only one way is possible, so it is explained, not asked
        self.assertIn('Outside access will use port forwarding', text)
        self.assertIn(SKIP + ' DNS for elsewhere.example.net is not managed by Cloudflare', text)
        self.assertIn('python3 setup.py --remote-access', text)
        self.assertIn(wizard.OTHER_DOMAIN_QUERY, text)       # the address is known, so a domain is only asked for on request
        self.assertNotIn(wizard.DOMAIN_QUERY, text)

    def test_user_can_name_a_cloudflare_domain_updated_some_other_way(self):
        env = self.env([(wizard.OTHER_DOMAIN_QUERY, True), ('Your domain, e.g. example.com', 'example.com'), (wizard.METHOD_QUERY, 1)],
                       names=['pi.elsewhere.example.net'])
        self.assertEqual(wizard.choose_mode(env.ctx), 'tunnel')
        self.assertEqual(env.ctx.cloudflare_base, 'example.com')
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w._plan_tunnel()
        self.assertEqual(w.new['TUNNEL_HOSTNAME'], 'rwsols.example.com')

    def test_domain_is_required_and_a_name_that_does_not_exist_is_asked_again(self):
        env = self.env([OUTSIDE, ('Your domain, e.g. example.com', ['', 'nothing-here.example.org', 'elsewhere.example.net'])])
        os.remove(env.config_path)
        self.assertEqual(wizard.choose_mode(env.ctx), 'port_forward')
        text = env.ui.text()
        self.assertNotIn('blank', text)
        self.assertIn('Enter your domain, for example example.com.', text)
        self.assertIn('nothing-here.example.org does not appear to be a registered domain', text)
        self.assertIn('Check the spelling and enter the domain again.', text)
        self.assertNotIn(wizard.METHOD_QUERY, text)

    def test_domain_on_another_dns_provider_is_reported_with_its_nameservers(self):
        env = self.env([OUTSIDE, ('Your domain, e.g. example.com', 'elsewhere.example.net')])
        os.remove(env.config_path)
        self.assertEqual(wizard.choose_mode(env.ctx), 'port_forward')
        text = env.ui.text()
        for line in ('Checking elsewhere.example.net ...', 'Found elsewhere.example.net.',
                     'Nameservers: ns1.dns-host.example, ns2.dns-host.example',
                     'DNS provider: not Cloudflare. Only port forwarding is available.',
                     SKIP + ' DNS for elsewhere.example.net is not managed by Cloudflare'):
            self.assertIn(line, text)
        self.assertEqual(text.count(wizard.DOMAIN_QUERY), 1)      # a real domain elsewhere is accepted, not asked again
        self.assertEqual(env.ctx.checked_domain, {'name': 'elsewhere.example.net', 'provider': 'other'})

    def fresh(self, rules):
        env = self.env(list(rules))
        os.remove(env.config_path)
        return env

    def test_fresh_install_is_asked_what_kind_of_address_it_has_first(self):
        env = self.fresh([OUTSIDE, ('Your domain, e.g. example.com', 'example.com')])
        self.assertEqual(wizard.choose_mode(env.ctx), 'tunnel')   # own domain on Cloudflare: the tunnel is the default
        text = env.ui.text()
        self.assertLess(text.index(wizard.WHERE_QUERY), text.index(wizard.ADDRESS_QUERY))
        self.assertLess(text.index(wizard.ADDRESS_QUERY), text.index('Checking your domain'))
        self.assertLess(text.index('Checking your domain'), text.index(wizard.METHOD_QUERY))
        self.assertNotIn('Cloudflare', text.split(wizard.WHERE_QUERY)[0])   # the first step is only about home versus outside
        for line in ('Checking example.com ...', 'Found example.com.', 'Nameservers: ada.ns.cloudflare.com, bob.ns.cloudflare.com'):
            self.assertIn(line, text)
        self.assertIn('DNS provider: Cloudflare. Cloudflare Tunnel is available.', text)
        self.assertEqual(self.offered, [wizard.WHERE_OPTIONS, wizard.ADDRESS_OPTIONS, wizard.METHOD_OPTIONS])

    def test_free_dynamic_dns_name_means_port_forwarding_and_no_cloudflare_questions(self):
        env = self.fresh([OUTSIDE, (wizard.ADDRESS_QUERY, 2)])
        self.assertEqual(wizard.choose_mode(env.ctx), 'port_forward')
        self.assertEqual(self.offered, [wizard.WHERE_OPTIONS, wizard.ADDRESS_OPTIONS])
        text = env.ui.text()
        self.assertIn('This works with any address, including a free dynamic DNS name.', text)
        self.assertIn(SKIP + ' A free dynamic DNS name belongs to its provider', text)
        self.assertIn('you need a domain of your own', text)
        self.assertNotIn('Your domain, e.g. example.com', text)
        self.assertNotIn('Checking your domain', text)

    def test_home_only_skips_every_other_question(self):
        env = self.fresh([])
        self.assertEqual(wizard.choose_mode(env.ctx), 'none')   # a fresh install defaults to home only
        self.assertEqual(self.offered, [wizard.WHERE_OPTIONS])
        text = env.ui.text()
        for word in ('Cloudflare', 'domain of your own', 'Port forwarding'):
            self.assertNotIn(word, text)

    def test_known_dynamic_dns_names_skip_the_address_question(self):
        env = self.env(names=['home.example.com'])
        wizard.choose_mode(env.ctx)
        self.assertNotIn(wizard.ADDRESS_QUERY, env.ui.text())

    def test_failed_lookup_keeps_the_option(self):
        env = self.env(names=['home.example.com'])
        env.cf.ns = None
        wizard.choose_mode(env.ctx)
        self.assertEqual(self.labels(), wizard.METHOD_OPTIONS)
        text = env.ui.text()
        self.assertIn('Could not check who manages DNS for home.example.com', text)
        self.assertIn('offered anyway', text)
        self.assertNotIn(SKIP, text)

    def test_existing_tunnel_install_is_never_stranded(self):
        env = self.env(names=['pi.elsewhere.example.net'])
        with open(env.ctx.state_path, 'w') as f:
            json.dump({'mode': 'tunnel', 'tunnel': {'hostname': 'rwsols.elsewhere.example.net'}}, f)
        self.assertEqual(wizard.choose_mode(env.ctx), 'tunnel')
        self.assertEqual(self.labels(), wizard.METHOD_OPTIONS)

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
