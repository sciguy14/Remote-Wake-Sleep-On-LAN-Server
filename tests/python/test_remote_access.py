import json
import os
import re
import unittest

import fakes
from fakes import API_TOKEN, TUNNEL_SECRET, Env, FakeCloudflare, FakeRunner, make_tunnel_token

from rwsols_setup import access, apache, hosts, phpconfig, state, tunnel, wizard
from rwsols_setup.cloudflare_api import CloudflareAPI
from rwsols_setup.errors import ApiError, ConflictError, SetupError

HOST = 'wol.example.com'
TUNNEL_RULES = [('Public hostname', HOST)]


def run_tunnel(env, mode='tunnel'):
    w = wizard.RemoteAccessWizard(env.ctx, mode, [], None)
    w.plan()
    w.prepare_cloud()
    w.write_config()
    w.apply_local()
    return w


def rerun_env(env, rules):
    env.make_ctx(rules)
    env.ctx.env['CLOUDFLARE_API_TOKEN'] = API_TOKEN


class CreateTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(TUNNEL_RULES)
        self.addCleanup(self.env.cleanup)

    def test_create_from_scratch(self):
        run_tunnel(self.env)
        cf, runner = self.env.cf, self.env.runner
        self.assertEqual(len(cf.tunnels), 1)
        self.assertEqual(cf.tunnels[0]['config_src'], 'cloudflare')
        cfg = cf.configs[cf.tunnels[0]['id']]
        self.assertEqual(cfg['ingress'], [{'hostname': HOST, 'service': 'http://127.0.0.1:8080'}, {'service': 'http_status:404'}])
        self.assertEqual(len(cf.dns), 1)
        rec = cf.dns[0]
        self.assertEqual((rec['type'], rec['name'], rec['proxied']), ('CNAME', HOST, True))
        self.assertEqual(rec['content'], cf.tunnels[0]['id'] + '.cfargotunnel.com')
        env_file, mode = runner.files[tunnel.ENV_PATH]
        self.assertEqual(mode, '0600')
        self.assertIn(b'TUNNEL_TOKEN=', env_file)
        self.assertIn(TUNNEL_SECRET.encode(), base64_decode(env_file.split(b'=', 1)[1].strip()))
        unit = runner.files[tunnel.SERVICE_PATH][0].decode()
        self.assertIn('EnvironmentFile=/etc/cloudflared/rwsols-tunnel.env', unit)
        self.assertIn('ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run', unit)
        self.assertNotIn('TUNNEL_TOKEN', unit)
        self.assertIn(apache.TUNNEL_SITE_PATH, runner.files)
        self.assertIn('rwsols-cloudflared', runner.active)
        text = self.env.config()
        self.assertIn("$TUNNEL_HOSTNAME = 'wol.example.com';", text)
        st = json.loads(self.env.state_text())
        self.assertEqual(st['mode'], 'tunnel')
        self.assertTrue(st['tunnel']['created_tunnel'] and st['tunnel']['dns_created'])
        # cloudflared service install <token> must never be used
        self.assertFalse(any('service' in a and 'install' in a for a in runner.argvs()))

    def test_secrets_never_leak(self):
        run_tunnel(self.env)
        seen = self.env.everything_observable()
        token = make_tunnel_token(self.env.cf.account, self.env.cf.tunnels[0]['id'])
        for secret in (API_TOKEN, token, TUNNEL_SECRET):
            self.assertNotIn(secret, seen)
        # tunnel token only ever travels as stdin of the env-file write
        carriers = [c for c in self.env.runner.calls if c['input'] and token.encode() in c['input']]
        self.assertEqual([c['argv'][1] for c in carriers], [tunnel.ENV_PATH])
        self.assertNotIn('CLOUDFLARE_API_TOKEN', self.env.ctx.env)

    def test_api_errors_do_not_contain_token(self):
        cf = FakeCloudflare()
        cf.fail_with = (403, 'Authentication error for ' + API_TOKEN)
        api = CloudflareAPI(API_TOKEN, cf)
        with self.assertRaises(ApiError) as cm:
            api.get('/zones', {'name': 'example.com'})
        self.assertNotIn(API_TOKEN, str(cm.exception))
        self.assertIn('Authentication error', str(cm.exception))

    def test_rerun_is_idempotent(self):
        run_tunnel(self.env)
        rerun_env(self.env, TUNNEL_RULES)
        before_api, before_run = len(self.env.cf.calls), len(self.env.runner.calls)
        run_tunnel(self.env)
        new_api = self.env.cf.calls[before_api:]
        self.assertEqual([c for c in new_api if c[0] != 'GET'], [])
        new_run = self.env.runner.calls[before_run:]
        self.assertEqual([c['argv'] for c in new_run if not c['read_only']], [])
        self.assertEqual(len(self.env.cf.tunnels), 1)
        self.assertEqual(len(self.env.cf.dns), 1)

    def test_existing_foreign_ingress_is_kept(self):
        cf = self.env.cf
        cf.tunnels.append({'id': 'tun-x', 'name': 'rwsols', 'config_src': 'cloudflare', 'status': 'healthy', 'deleted_at': None})
        cf.configs['tun-x'] = {'ingress': [{'hostname': 'other.example.com', 'service': 'http://localhost:3000'}, {'service': 'http_status:404'}]}
        run_tunnel(self.env)
        rules = cf.configs['tun-x']['ingress']
        self.assertEqual([r.get('hostname') for r in rules], ['other.example.com', HOST, None])
        self.assertIn('other.example.com', self.env.ui.text())
        self.assertFalse(json.loads(self.env.state_text())['tunnel']['created_tunnel'])

    def test_conflicting_dns_stops_and_shows_record(self):
        self.env.cf.dns.append({'id': 'dns-old', 'type': 'A', 'name': HOST, 'content': '203.0.113.9', 'proxied': False})
        with self.assertRaises(ConflictError):
            run_tunnel(self.env)
        self.assertIn('203.0.113.9', self.env.ui.text())
        self.assertEqual([c for c in self.env.cf.mutations if 'dns_records' in c[1]], [])
        self.assertEqual(self.env.runner.files.get(tunnel.ENV_PATH), None)

    def test_force_overwrites_conflicting_dns(self):
        env = Env(TUNNEL_RULES, force=True)
        self.addCleanup(env.cleanup)
        env.cf.dns.append({'id': 'dns-old', 'type': 'A', 'name': HOST, 'content': '203.0.113.9', 'proxied': False})
        run_tunnel(env)
        self.assertEqual([(r['type'], r['content'].endswith('.cfargotunnel.com')) for r in env.cf.dns], [('CNAME', True)])

    def test_dry_run_changes_nothing(self):
        env = Env(TUNNEL_RULES, dry_run=True)
        self.addCleanup(env.cleanup)
        before = env.config()
        run_tunnel(env)
        self.assertEqual(env.cf.mutations, [])
        self.assertEqual(env.runner.mutations, [])
        self.assertEqual(env.config(), before)
        self.assertEqual(env.state_text(), '')
        text = env.ui.text()
        self.assertIn('[dry-run]', text)
        # A dry run never claims that something was done.
        for claim in ('Created tunnel', 'Created proxied CNAME', 'Set tunnel ingress', 'config.php updated',
                      'allowlist applied', 'is set up', 'Installing cloudflared'):
            self.assertNotIn(claim, text)
        for plan in ("[dry-run] would create tunnel", '[dry-run] would create proxied CNAME', '[dry-run] would update config.php',
                     'would be set up at', 'Nothing was changed'):
            self.assertIn(plan, text)
        self.assertEqual(env.cf.tunnels, [])

    def test_manual_token_path_makes_no_api_calls(self):
        token = make_tunnel_token('acct111', 'tun-manual')
        env = Env([('Public hostname', HOST), ('How should the tunnel', 2)], secrets=[token], token=False)
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        self.assertEqual(env.cf.calls, [])
        self.assertEqual(env.runner.files[tunnel.ENV_PATH][1], '0600')
        self.assertNotIn(token, env.everything_observable())
        self.assertIn('Service:  HTTP  127.0.0.1:8080', env.ui.text())

    def test_manual_token_rejects_garbage(self):
        with self.assertRaises(SetupError):
            tunnel.parse_tunnel_token('not-a-token')

    def test_cloudflared_apt_install_and_skip(self):
        run_tunnel(self.env)
        argvs = self.env.runner.argvs()
        self.assertIn(['apt-get', 'install', '-y', 'cloudflared'], argvs)
        self.assertEqual(self.env.runner.files[tunnel.APT_LIST_PATH][0].decode(), tunnel.APT_LIST)
        env2 = Env(TUNNEL_RULES, runner=FakeRunner(installed=True))
        self.addCleanup(env2.cleanup)
        run_tunnel(env2)
        self.assertNotIn(['apt-get', 'install', '-y', 'cloudflared'], env2.runner.argvs())

    def test_armv6_detected(self):
        env = Env(TUNNEL_RULES, runner=FakeRunner(machine='armv6l', deb_arch='armhf'))
        self.addCleanup(env.cleanup)
        with self.assertRaises(SetupError) as cm:
            run_tunnel(env)
        self.assertIn('ARMv6', str(cm.exception))

    def test_origin_port_busy_asks_again(self):
        env = Env([('Public hostname', HOST), ('Local port', ['8080', '8081'])])
        self.addCleanup(env.cleanup)
        env.ctx.port_free = lambda b, p: p != 8080
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        self.assertEqual(w.new['TUNNEL_ORIGIN_PORT'], 8081)


class AccessTests(unittest.TestCase):
    RULES = TUNNEL_RULES + [('Enable Cloudflare Access', True), ('Emails allowed', 'User@Example.com')]

    def test_login_length_defaults_to_one_month_on_app_and_policy(self):
        env = Env(self.RULES)
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        self.assertIn(access.SESSION_QUERY, env.ui.out)
        self.assertEqual(env.cf.apps[0]['session_duration'], '720h')
        self.assertEqual(env.cf.policies[0]['session_duration'], '720h')
        self.assertIn('"session_duration": "720h"', env.state_text())

    def test_login_length_choice_is_applied(self):
        for pick, want in ((1, '24h'), (2, '168h'), (3, '720h')):
            env = Env(self.RULES + [(access.SESSION_QUERY, pick)])
            self.addCleanup(env.cleanup)
            run_tunnel(env)
            self.assertEqual((env.cf.apps[0]['session_duration'], env.cf.policies[0]['session_duration']), (want, want))

    def test_rerun_keeps_the_saved_login_length_and_can_change_it(self):
        env = Env(self.RULES + [(access.SESSION_QUERY, 2)])
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        app_before = dict(env.cf.apps[0])
        rerun_env(env, self.RULES)                                   # no answer: the saved 1 week is the default
        run_tunnel(env)
        self.assertEqual(env.cf.apps[0]['session_duration'], '168h')
        rerun_env(env, self.RULES + [(access.SESSION_QUERY, 1)])
        run_tunnel(env)
        app = env.cf.apps[0]
        self.assertEqual((app['session_duration'], env.cf.policies[0]['session_duration']), ('24h', '24h'))
        self.assertEqual(len(env.cf.apps), 1)
        for k in ('id', 'aud', 'domain', 'type', 'name'):
            self.assertEqual(app[k], app_before[k])
        self.assertEqual([p['id'] for p in app['policies']], [p['id'] for p in app_before['policies']])

    def test_login_length_of_an_app_the_wizard_did_not_create_is_left_alone(self):
        env = Env(self.RULES)
        self.addCleanup(env.cleanup)
        env.cf.apps.append({'id': 'app-theirs', 'type': 'self_hosted', 'domain': HOST, 'aud': 'a' * 64,
                            'session_duration': '24h', 'policies': []})
        run_tunnel(env)
        self.assertEqual(env.cf.apps[0]['session_duration'], '24h')
        self.assertIn('was not changed', env.ui.text())

    def test_access_automatic_on(self):
        env = Env(self.RULES)
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        cf = env.cf
        self.assertEqual(len(cf.apps), 1)
        app = cf.apps[0]
        self.assertEqual((app['type'], app['domain']), ('self_hosted', HOST))
        self.assertEqual(cf.policies[0]['decision'], 'allow')
        self.assertEqual(cf.policies[0]['include'], [{'email': {'email': 'user@example.com'}}])
        self.assertEqual(app['policies'][0]['id'], cf.policies[0]['id'])
        text = env.config()
        self.assertIn("$ACCESS_TEAM_DOMAIN = 'example.cloudflareaccess.com';", text)
        self.assertIn("$ACCESS_AUD = '%s';" % app['aud'], text)
        conf = env.runner.files[apache.ALLOWED_CONF_PATH][0].decode()
        self.assertIn('Cf-Access-Jwt-Assertion', conf)

    def test_access_service_token_shown_once_not_stored(self):
        env = Env(self.RULES + [('service token', True)])
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        self.assertEqual(env.ui.text().count('CLIENTSECRET-xyz'), 1)
        self.assertNotIn('CLIENTSECRET-xyz', env.state_text() + env.config())
        self.assertEqual([p['decision'] for p in env.cf.policies], ['allow', 'non_identity'])
        # rerun does not mint another one
        rerun_env(env, self.RULES + [('service token', True)])
        run_tunnel(env)
        self.assertEqual(len(env.cf.service_tokens), 1)
        self.assertEqual(env.ui.text().count('CLIENTSECRET-xyz'), 0)

    def test_access_off_on_rerun_clears_keys_and_deletes_app(self):
        env = Env(self.RULES)
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        rerun_env(env, TUNNEL_RULES + [('Enable Cloudflare Access', False)])
        run_tunnel(env)
        self.assertIn('$ACCESS_TEAM_DOMAIN = NULL;', env.config())
        self.assertIn('$ACCESS_AUD = NULL;', env.config())
        self.assertEqual(env.cf.apps, [])
        self.assertNotIn('Cf-Access-Jwt-Assertion', (env.runner.read_file(apache.ALLOWED_CONF_PATH) or b'').decode())

    def test_existing_app_is_adopted_and_not_deleted(self):
        env = Env(self.RULES)
        self.addCleanup(env.cleanup)
        env.cf.apps.append({'id': 'app-pre', 'type': 'self_hosted', 'domain': HOST, 'aud': 'a' * 64, 'policies': []})
        run_tunnel(env)
        self.assertEqual(len(env.cf.apps), 1)
        self.assertIn("$ACCESS_AUD = '%s';" % ('a' * 64), env.config())
        self.assertFalse(json.loads(env.state_text())['access']['app_created'])

    def test_manual_access_values_validated(self):
        token = make_tunnel_token('acct111', 'tun-manual')
        aud = 'ab' * 32
        rules = [('Public hostname', HOST), ('How should the tunnel', 2), ('Enable Cloudflare Access', True),
                 ('Team domain', ['evil.com', 'http://example.cloudflareaccess.com/']), ('AUD', ['short', aud])]
        env = Env(rules, secrets=[token], token=False)
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        self.assertIn("$ACCESS_TEAM_DOMAIN = 'example.cloudflareaccess.com';", env.config())
        self.assertIn("$ACCESS_AUD = '%s';" % aud, env.config())
        self.assertEqual(env.cf.calls, [])

    def test_validators(self):
        self.assertEqual(access.validate_team_domain('Team.CloudflareAccess.com'), 'team.cloudflareaccess.com')
        for bad in ('example.com', 'a b.cloudflareaccess.com', ''):
            with self.assertRaises(ValueError):
                access.validate_team_domain(bad)
        with self.assertRaises(ValueError):
            access.validate_aud('xyz')
        with self.assertRaises(ValueError):
            access.parse_identities('', '')
        self.assertEqual(access.parse_identities('a@example.com', '@corp.example.com'), (['a@example.com'], ['corp.example.com']))


class RemoveTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(TUNNEL_RULES + [('Enable Cloudflare Access', True), ('Emails allowed', 'u@example.com'),
                                       ('Enable the hostname allowlist', 1)])
        self.addCleanup(self.env.cleanup)
        run_tunnel(self.env)
        self.assertIn(HOST, self.env.config())

    def do_remove(self):
        rerun_env(self.env, [('Tear down', True), ('Provide an API token', True), ('uninstall the cloudflared', False)])
        w = wizard.RemoteAccessWizard(self.env.ctx, 'none')
        w.remove_tunnel()

    def test_remove_deletes_only_wizard_created_things(self):
        self.do_remove()
        cf, runner = self.env.cf, self.env.runner
        self.assertEqual((cf.tunnels, cf.dns, cf.apps, cf.policies), ([], [], [], []))
        for p in (tunnel.ENV_PATH, tunnel.SERVICE_PATH, apache.TUNNEL_SITE_PATH):
            self.assertNotIn(p, runner.files)
        self.assertNotIn('rwsols-cloudflared', runner.active)
        text = self.env.config()
        self.assertIn('$TUNNEL_HOSTNAME = NULL;', text)
        self.assertIn('$ACCESS_AUD = NULL;', text)
        self.assertNotIn(HOST, re.search(r'\$ALLOWED_HOSTS = [^;]*;', text).group(0))
        self.assertEqual(json.loads(self.env.state_text())['mode'], 'none')
        self.assertTrue(runner.installed)  # apt package kept: user said no

    def test_dns_record_pointing_elsewhere_is_left_alone(self):
        self.env.cf.dns[0]['content'] = 'elsewhere.example.net'
        self.do_remove()
        self.assertEqual(len(self.env.cf.dns), 1)
        self.assertIn('LEFT ALONE', self.env.ui.text())

    def test_preexisting_tunnel_not_deleted(self):
        st = json.loads(self.env.state_text())
        st['tunnel']['created_tunnel'] = False
        with open(os.path.join(self.env.dir, '.rwsols_remote_access.json'), 'w') as f:
            json.dump(st, f)
        self.do_remove()
        self.assertEqual(len(self.env.cf.tunnels), 1)


class AllowlistTests(unittest.TestCase):
    def test_allowlist_written_and_conf_applied(self):
        env = Env([('Enable the hostname allowlist', 1), ('Allowed host names', ['raspberrypi.local', 'A.example.com'])])
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'none', [], None)
        w.plan()
        w.write_config()
        w.apply_local()
        self.assertIn("$ALLOWED_HOSTS = array('raspberrypi.local', 'a.example.com');", env.config())
        self.assertIn(apache.ALLOWED_CONF_PATH, env.runner.files)
        self.assertIn(['a2enconf', 'rwsols-allowed-hosts'], env.runner.argvs())

    def test_tunnel_hostname_always_added(self):
        env = Env(TUNNEL_RULES + [('Enable the hostname allowlist', 1), ('Allowed host names', ['raspberrypi.local'])])
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        self.assertIn(HOST, w.new['ALLOWED_HOSTS'])

    def test_no_allowlist_leaves_existing_install_config_untouched(self):
        env = Env()
        self.addCleanup(env.cleanup)
        before = env.config()
        w = wizard.RemoteAccessWizard(env.ctx, 'port_forward', ['home.example.com'], None)
        w.plan()
        w.write_config()
        w.apply_local()
        self.assertEqual(env.config(), before)
        self.assertEqual([c for c in env.runner.mutations], [])

    def test_disable_removes_conf(self):
        env = Env([('Enable the hostname allowlist', 1), ('Allowed host names', ['a.example.com'])])
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'none', [], None)
        w.plan(); w.write_config(); w.apply_local()
        rerun_env(env, [('Enable the hostname allowlist', 2)])
        w = wizard.RemoteAccessWizard(env.ctx, 'none', [], None)
        w.plan(); w.write_config(); w.apply_local()
        self.assertIn('$ALLOWED_HOSTS = array();', env.config())
        self.assertNotIn(apache.ALLOWED_CONF_PATH, env.runner.files)
        self.assertIn(['a2disconf', 'rwsols-allowed-hosts'], env.runner.argvs())

    def test_configtest_failure_rolls_back(self):
        env = Env()
        self.addCleanup(env.cleanup)
        env.runner.configtest_ok = False
        with self.assertRaises(SetupError):
            apache.apply_allowed_hosts(env.ctx, apache.render_allowed_hosts_conf(['a.example.com']))
        self.assertNotIn(apache.ALLOWED_CONF_PATH, env.runner.files)
        self.assertNotIn(['systemctl', 'reload', 'apache2'], env.runner.argvs())


class PhpConfigTests(unittest.TestCase):
    TEXT = ("<?php\r\n// $ALLOWED_HOSTS = array('commented');\r\n$USE_HTTPS = false;\r\n"
            "  $A = array(\"x\",\r\n  'y');  // trailing\r\n$S = 'it\\'s';\r\n$N = NULL;\r\n$I = 7;\r\n?>\r\n")

    def test_read_values(self):
        g = lambda n: phpconfig.get_value(self.TEXT, n)
        self.assertEqual((g('USE_HTTPS'), g('A'), g('S'), g('N'), g('I')), (False, ['x', 'y'], "it's", None, 7))
        self.assertEqual(phpconfig.get_value(self.TEXT, 'ALLOWED_HOSTS', 'dflt'), 'dflt')

    def test_update_in_place_preserves_other_bytes(self):
        out = phpconfig.update_text(self.TEXT, {'I': 9, 'A': ['a.example.com']})
        self.assertEqual(out, self.TEXT.replace('$I = 7;', '$I = 9;').replace('array("x",\r\n  \'y\');', "array('a.example.com');"))

    def test_append_missing_in_block_before_close_tag(self):
        out = phpconfig.update_text(self.TEXT, {'TUNNEL_HOSTNAME': HOST, 'ALLOW_LOCALHOST': True, 'X': None})
        self.assertIn(phpconfig.BLOCK_HEADER, out)
        self.assertTrue(out.index('$TUNNEL_HOSTNAME') < out.index('?>'))
        self.assertTrue(out.startswith(self.TEXT[:self.TEXT.index('?>')]))
        self.assertIn("$ALLOW_LOCALHOST = true;\r\n", out)
        self.assertEqual(phpconfig.get_value(out, 'TUNNEL_HOSTNAME'), HOST)

    def test_escaping_round_trip(self):
        nasty = "a\\b'c\\'d$e\"f"
        out = phpconfig.update_text(self.TEXT, {'S': nasty, 'L': [nasty, 'z']})
        self.assertEqual(phpconfig.get_value(out, 'S'), nasty)
        self.assertEqual(phpconfig.get_value(out, 'L'), [nasty, 'z'])
        self.assertIn("'a\\\\b\\'c\\\\\\'d$e\"f'", out)
        with self.assertRaises(SetupError):
            phpconfig.render_value('line\nbreak')

    def test_value_types_render(self):
        self.assertEqual([phpconfig.render_value(v) for v in (None, True, False, 8080, [], ['a'])],
                         ['NULL', 'true', 'false', '8080', 'array()', "array('a')"])

    def test_no_closing_tag_appends(self):
        out = phpconfig.update_text('<?php\n$A = 1;\n', {'B': 2})
        self.assertTrue(out.endswith('$B = 2;\n'))

    def test_hostname_validation(self):
        self.assertEqual(hosts.validate_hostname('WOL.Example.com.'), HOST)
        self.assertEqual(hosts.validate_hostname('::1'), '[::1]')
        self.assertEqual(hosts.validate_hostname('192.168.1.5'), '192.168.1.5')
        for bad in ("a.com'; system('x'); //", 'a b.com', '*.example.com', '999.1.1.1', '-a.com', 'a..com', '', 'a.com/x', '[zzz]'):
            with self.assertRaises(ValueError, msg=bad):
                hosts.validate_hostname(bad)


def base64_decode(b):
    import base64
    return base64.b64decode(b + b'=' * (-len(b) % 4))


class ApacheRenderTests(unittest.TestCase):
    LIST = ['wol.example.com', 'raspberrypi.local', '[::1]', '192.168.1.5']
    TABLE = [
        ('wol.example.com', True), ('WOL.Example.COM', True), ('wol.example.com:8443', True), ('wol.example.com.', True),
        ('wol.example.com.:80', True), ('wolXexample.com', False), ('evil.com', False), ('', False), (None, False),
        ('sub.wol.example.com', False), ('wol.example.com.evil.com', False), ('192.168.1.5', True), ('192.168.1.6', False),
        ('192x168x1x5', False), ('[::1]:80', True), ('[::1]', True), ('::1', False), ('localhost', False),
        ('wol.example.com:', False), ('wol.example.com:80:80', False), ('wol.example.com..', False),
        ('wol.example.com, evil.com', False), ('raspberrypi.local', True), ('raspberrypi', False),
    ]

    @staticmethod
    def conf_regex(conf):
        return re.compile(re.search(r'<If "%\{HTTP_HOST\} !~ m#(.*?)#i', conf).group(1), re.I)

    def apache_allows(self, rx, host):
        # An absent Host header is an empty %{HTTP_HOST}
        return rx.search(host or '') is not None

    def test_regex_agrees_with_contract_semantics(self):
        rx = self.conf_regex(apache.render_allowed_hosts_conf(self.LIST))
        for host, want in self.TABLE:
            self.assertEqual(hosts.is_allowed(host, self.LIST), want, 'reference ' + repr(host))
            self.assertEqual(self.apache_allows(rx, host), want, 'apache regex ' + repr(host))

    def test_allow_localhost(self):
        conf = apache.render_allowed_hosts_conf(['a.example.com'], allow_localhost=True)
        rx = self.conf_regex(conf)
        for h in ('localhost', 'LOCALHOST:80', '127.0.0.1', '127.0.0.1:8080', '[::1]:80', 'a.example.com'):
            self.assertTrue(self.apache_allows(rx, h), h)
            self.assertTrue(hosts.is_allowed(h, ['a.example.com'], True), h)
        self.assertFalse(self.apache_allows(rx, '127.0.0.2'))
        self.assertFalse(self.apache_allows(self.conf_regex(apache.render_allowed_hosts_conf(['a.example.com'])), 'localhost'))

    def test_dots_are_escaped(self):
        conf = apache.render_allowed_hosts_conf(['wol.example.com'])
        self.assertIn(r'wol\.example\.com', conf)
        self.assertFalse(self.apache_allows(self.conf_regex(conf), 'wolXexample.com'))

    def test_empty_list_without_access_means_no_conf(self):
        self.assertIsNone(apache.render_allowed_hosts_conf([]))
        self.assertIsNone(apache.render_allowed_hosts_conf([], tunnel_hostname=HOST))

    def test_conf_structure(self):
        conf = apache.render_allowed_hosts_conf(self.LIST)
        self.assertIn('Generated by RWSOLS setup.py', conf)
        self.assertIn('python3 setup.py --remote-access', conf)
        self.assertIn('ErrorDocument 403 /host_reject.php', conf)
        self.assertIn("%{REQUEST_URI} != '/host_reject.php'", conf)
        self.assertNotIn('Cf-Access', conf)

    def test_access_block(self):
        conf = apache.render_allowed_hosts_conf([HOST], tunnel_hostname=HOST, access_enabled=True)
        self.assertIn("-z req('Cf-Access-Jwt-Assertion')", conf)
        self.assertIn(r"wol\.example\.com", conf)
        self.assertNotIn('SERVER_ADDR', conf)
        site = apache.render_tunnel_site('127.0.0.1', 8080, access_enabled=True)
        self.assertIn("<If \"-z req('Cf-Access-Jwt-Assertion') && %{REQUEST_URI} != '/host_reject.php'\">", site)
        self.assertNotIn('Cf-Access', apache.render_tunnel_site('127.0.0.1', 8080))
        only_access = apache.render_allowed_hosts_conf([], tunnel_hostname=HOST, access_enabled=True)
        self.assertNotIn('ErrorDocument', only_access)

    def test_rejects_unvalidated_hosts(self):
        with self.assertRaises(ValueError):
            apache.render_allowed_hosts_conf(["x#i'] || true || ['"])

    def test_tunnel_site(self):
        site = apache.render_tunnel_site('127.0.0.1', 8080)
        self.assertIn('Listen 127.0.0.1:8080', site)
        self.assertIn('<VirtualHost 127.0.0.1:8080>', site)
        self.assertIn('SetEnv RWSOLS_TUNNEL_ORIGIN 1', site)
        self.assertIn('<Directory /var/www/html/>', site)
        with self.assertRaises(SetupError):
            apache.render_tunnel_site('not-an-ip', 8080)
        with self.assertRaises(SetupError):
            apache.render_tunnel_site('127.0.0.1', 99999)


class MiscTests(unittest.TestCase):
    def test_ddns_overlap_warning(self):
        conf = "# c\nprotocol=cloudflare\nzone=example.com\nlogin=token\npassword=x\nwol.example.com, home.example.com\n"
        self.assertTrue(tunnel.ddns_overlap('WOL.example.com', conf))
        self.assertFalse(tunnel.ddns_overlap('other.example.com', conf))
        self.assertFalse(tunnel.ddns_overlap('example.com', conf))
        self.assertFalse(tunnel.ddns_overlap(HOST, '#wol.example.com\n'))

    def test_ddns_names_listed_but_unticked_outside_port_forwarding(self):
        ddclient = (b'protocol=dyndns2\nlogin=u\npassword=p\nhome.example.com\n', None)
        config = fakes.FAKE_CONFIG.replace('?>', "$ALLOWED_HOSTS = array('home.example.com', 'raspberrypi.local');\n?>")
        self.assertIn('$ALLOWED_HOSTS', config)

        runner = FakeRunner()
        runner.files['/etc/ddclient.conf'] = ddclient
        env = Env(TUNNEL_RULES, runner=runner, config=config)
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        self.assertNotIn('Not suggesting', env.ui.text())
        opts = dict((o.value, o) for o in env.ui.many_calls[-1]['options'])
        self.assertFalse(opts['home.example.com'].checked)
        self.assertIn('port forward', opts['home.example.com'].why)
        self.assertNotIn('home.example.com', w.new['ALLOWED_HOSTS'])
        self.assertIn('raspberrypi.local', w.new['ALLOWED_HOSTS'])

        runner = FakeRunner()
        runner.files['/etc/ddclient.conf'] = ddclient
        env = Env(runner=runner, config=config)
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'port_forward', ['home.example.com'], None)
        w.plan()
        opts = dict((o.value, o) for o in env.ui.many_calls[-1]['options'])
        self.assertTrue(opts['home.example.com'].checked)
        self.assertIn('home.example.com', w.new['ALLOWED_HOSTS'])

    def test_wizard_warns_on_ddclient_overlap(self):
        runner = FakeRunner()
        runner.files['/etc/ddclient.conf'] = (b'protocol=dyndns2\nlogin=u\npassword=p\nwol.example.com,home.example.com\n', None)
        env = Env(TUNNEL_RULES, runner=runner)
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        self.assertIn('ddclient', env.ui.text())
        self.assertNotIn('p\n', ' '.join(env.ui.out))  # ddclient password never echoed

    def test_default_mode_new_vs_existing_install(self):
        import shutil, tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(state.default_mode(d), 'none')
        open(os.path.join(d, '._04_setup_ddns'), 'w').close()
        self.assertEqual(state.default_mode(d), 'port_forward')
        os.remove(os.path.join(d, '._04_setup_ddns'))
        os.makedirs(os.path.join(d, 'www', 'html'))
        open(os.path.join(d, 'www', 'html', 'config.php'), 'w').close()
        self.assertEqual(state.default_mode(d), 'port_forward')
        with open(os.path.join(d, '.rwsols_remote_access.json'), 'w') as f:
            json.dump({'mode': 'tunnel'}, f)
        self.assertEqual(state.default_mode(d), 'tunnel')

    def test_choose_mode_defaults(self):
        env = Env()
        self.addCleanup(env.cleanup)
        self.assertEqual(wizard.choose_mode(env.ctx), 'port_forward')  # config.php exists => existing install
        os.remove(env.config_path)
        self.assertEqual(wizard.choose_mode(env.ctx), 'none')

    def test_token_scope_text_present(self):
        env = Env(TUNNEL_RULES, token=False, secrets=[API_TOKEN])
        self.addCleanup(env.cleanup)
        run_tunnel(env)
        out = env.ui.text()
        self.assertIn('Cloudflare Tunnel > Edit', out)
        self.assertIn('DNS > Edit', out)
        self.assertNotIn(API_TOKEN, out)


if __name__ == '__main__':
    unittest.main()
