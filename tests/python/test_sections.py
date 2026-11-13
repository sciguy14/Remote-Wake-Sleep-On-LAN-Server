import importlib.util
import json
import os
import unittest
import warnings

import fakes
from fakes import API_TOKEN, Env, FakeRunner

from rwsols_setup import apache, reset, sections, tunnel, wizard
from rwsols_setup.state import default_mode

HOST = 'rwsols.example.com'
HASHED = fakes.FAKE_CONFIG.replace('$APPROVED_HASH = NULL;', "$APPROVED_HASH = '$2y$10$abcdefghijklmnopqrstuv';")


def load_setup_py():
    spec = importlib.util.spec_from_file_location('rwsols_setup_script_sections', os.path.join(fakes.ROOT, 'setup.py'))
    mod = importlib.util.module_from_spec(spec)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        spec.loader.exec_module(mod)
    return mod


def legacy(env):
    for name in sections.LEGACY_DOT_FILES:
        open(os.path.join(env.dir, name), 'w').close()


class Recorder(object):
    def __init__(self, env, fail=()):
        self.ran, self.fail, self.env = [], set(fail), env
        self.runners = dict((k, self._make(k)) for k in sections.KEYS)

    def _make(self, key):
        def run():
            self.ran.append(key)
            if key == 'config' and key not in self.fail:
                with open(self.env.config_path, 'w', newline='') as f:
                    f.write(HASHED)
            return key not in self.fail
        return run


class ChecklistTests(unittest.TestCase):
    def env(self, rules=(), config=fakes.FAKE_CONFIG):
        env = Env(list(rules), config=config)
        self.addCleanup(env.cleanup)
        return env

    def test_new_install_runs_every_section_in_order_then_reports_complete(self):
        env = self.env([(sections.MENU_QUERY, 1)])
        rec = Recorder(env)
        self.assertTrue(sections.run(env.ctx, rec.runners))
        self.assertEqual(rec.ran, sections.KEYS)
        text = env.ui.text()
        self.assertIn('Nothing is set up yet.', text)
        self.assertIn('Setup is complete.', text)
        self.assertNotIn(sections.MENU_QUERY, env.ui.out)           # a first run is not interrupted by the menu
        self.assertEqual(default_mode(env.dir), 'none')            # no mode chosen yet -> not "port forwarding"

    def test_existing_install_is_recorded_as_complete_and_nothing_runs(self):
        env = self.env(config=HASHED)
        legacy(env)
        rec = Recorder(env)
        self.assertTrue(sections.run(env.ctx, rec.runners))     # default answer: Quit
        self.assertEqual(rec.ran, [])
        st = json.loads(env.state_text())
        self.assertEqual(st['mode'], 'port_forward')
        self.assertTrue(all(st['sections'][k] for k in sections.KEYS))
        self.assertNotIn('Nothing is set up yet.', env.ui.text())

    def configured(self, with_tunnel=False):
        env = self.env(config=HASHED.replace('?>', "$COMPUTER_NAME = array('Desktop');\n$ALLOWED_HOSTS = array('raspberrypi.local');\n?>"))
        legacy(env)
        with open(os.path.join(fakes.ROOT, 'www', 'html', 'config_sample.php'), newline='') as f:
            self.sample = f.read()
        with open(os.path.join(os.path.dirname(env.config_path), 'config_sample.php'), 'w', newline='') as f:
            f.write(self.sample)
        r = env.runner
        r.files[reset.SERVED_CONFIG] = (b'old', '0640')
        r.files[apache.ALLOWED_CONF_PATH] = (b'x', '0644')
        if with_tunnel:
            r.files[tunnel.SERVICE_PATH] = (b'unit', '0644')
            r.files[tunnel.ENV_PATH] = (b'TUNNEL_TOKEN=x', '0600')
            r.files[apache.TUNNEL_SITE_PATH] = (b'Listen 127.0.0.1:8080', '0644')
            with open(env.ctx.state_path, 'w') as f:
                json.dump({'mode': 'tunnel', 'tunnel': {'id': 'abc', 'hostname': HOST, 'created_tunnel': True}, 'access': {},
                           'sections': dict((k, True) for k in sections.KEYS)}, f)
        return env

    def menu(self, env, answers):
        """Answer the main menu with `answers` in turn; every other choice takes its default."""
        self.menus = []
        real = env.ui.choose

        def choose(query, options, default=None):
            if query == sections.MENU_QUERY:
                self.menus.append(list(options))
                i = answers.pop(0)
                return i, options[i - 1]
            return real(query, options, default)
        env.ui.choose = choose

    def test_menu_offers_to_start_over_and_no_is_the_default(self):
        env = self.configured()
        self.menu(env, [2, 3])      # complete install: redo, start over, quit
        before = env.config()
        rec = Recorder(env)
        self.assertTrue(sections.run(env.ctx, rec.runners))
        self.assertEqual(self.menus[0][1], sections.RESET_OPTION)
        text = env.ui.text()
        self.assertIn('Nobody can log in to RWSOLS until you finish setup again.', text)
        self.assertIn('Nothing was changed.', text)
        self.assertEqual(env.config(), before)
        self.assertEqual(rec.ran, [])

    def test_start_over_resets_settings_keeps_a_backup_and_walks_every_section_again(self):
        env = self.configured(with_tunnel=True)
        env.make_ctx([(reset.CONFIRM_QUERY, True)])
        self.menu(env, [2])
        old = env.config()
        rec = Recorder(env)
        self.assertTrue(sections.run(env.ctx, rec.runners))
        self.assertEqual(rec.ran, sections.KEYS)                    # everything is asked again, in order
        r = env.runner
        for gone in (tunnel.SERVICE_PATH, tunnel.ENV_PATH, apache.TUNNEL_SITE_PATH, apache.ALLOWED_CONF_PATH):
            self.assertNotIn(gone, r.files)
        self.assertIn(['cp', os.path.join(os.path.dirname(env.config_path), 'config_sample.php'), reset.SERVED_CONFIG], r.argvs())
        backups = [f for f in os.listdir(env.dir) if f.startswith(reset.BACKUP_PREFIX)]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(env.dir, backups[0]), newline='') as f:
            self.assertEqual(f.read(), old)
        self.assertFalse(any(os.path.exists(os.path.join(env.dir, n)) for n in sections.LEGACY_DOT_FILES))
        text = env.ui.text()
        self.assertIn('in your Cloudflare account are NOT removed', text)
        self.assertIn('Reset done', text)
        self.assertNotIn(reset.DDNS_QUERY, text)                     # no ddclient configuration here, so no question
        self.assertNotIn('ddclient', ' '.join(' '.join(a) for a in r.argvs()))

    def test_start_over_leaves_dynamic_dns_alone_unless_asked(self):
        for answer, moved in ((False, False), (True, True)):
            env = self.configured()
            env.runner.files[reset.DDCLIENT_CONF] = (b'protocol=dyndns2\nhome.example.net\n', '0600')
            env.make_ctx([(reset.CONFIRM_QUERY, True), (reset.DDNS_QUERY, answer)])
            self.assertTrue(reset.run(env.ctx, None))
            self.assertEqual(['mv', '-f', reset.DDCLIENT_CONF, reset.DDCLIENT_BACKUP] in env.runner.argvs(), moved)
            self.assertNotIn('in your Cloudflare account', env.ui.text())
            with open(env.config_path, newline='') as f:
                self.assertEqual(f.read(), self.sample)

    def test_stopping_part_way_continues_where_it_left_off(self):
        env = self.env()
        rec = Recorder(env, fail=['https'])
        self.assertFalse(sections.run(env.ctx, rec.runners))
        self.assertEqual(rec.ran, ['base', 'remote', 'ddns', 'https'])
        self.assertIn('did not finish', env.ui.text())
        env.make_ctx([(sections.MENU_QUERY, 1)])
        rec2 = Recorder(env)
        sts = sections.statuses(env.ctx)
        self.assertEqual([k for k in sections.KEYS if sts[k].done], ['base', 'remote', 'ddns'])
        self.assertTrue(sections.run(env.ctx, rec2.runners))
        self.assertEqual(rec2.ran, ['https', 'allowlist', 'updates', 'config'])

    def test_one_section_can_be_run_again(self):
        env = self.env(config=HASHED)
        legacy(env)
        answers = [1, 3]                          # first menu: "Run one section again"; second menu: Quit

        def menu(query, options, default=None):
            if query == sections.MENU_QUERY:
                return answers.pop(0), None
            if query == sections.REDO_QUERY:
                return sections.KEYS.index('allowlist') + 1, None
            return (default or 1), None
        env.ui.choose = menu
        rec = Recorder(env)
        self.assertTrue(sections.run(env.ctx, rec.runners))
        self.assertEqual(rec.ran, ['allowlist'])

    def test_unmark_puts_a_section_back_on_the_list(self):
        env = self.env(config=HASHED)
        legacy(env)
        sections.load(env.ctx)
        sections.unmark(env.ctx, 'https')
        sts = sections.statuses(env.ctx)
        self.assertEqual([k for k in sections.KEYS if not sts[k].done], ['https'])

    def test_status_labels(self):
        env = self.env(config=HASHED)
        legacy(env)
        sections.load(env.ctx)
        sections.update(env.ctx, mode='tunnel', ddns='none')
        r = env.runner
        self.assertEqual(sections.statuses(env.ctx)['https'].label, 'plain HTTP')
        r.files[sections.DEFAULT_SITE] = (b'SSLEngine On', None)
        r.files[sections.SSL_MOD] = (b'', None)
        self.assertEqual(sections.statuses(env.ctx)['https'].label, 'self-signed certificate')
        sections.update(env.ctx, https={'kind': 'dns', 'names': ['pi.home.example.com']})
        sts = sections.statuses(env.ctx, ['example.com'])
        self.assertEqual(sts['https'].label, 'trusted certificate for pi.home.example.com (DNS challenge)')
        self.assertEqual(sts['remote'].label, 'Cloudflare Tunnel')
        self.assertEqual(sts['ddns'].label, 'not used')
        sections.update(env.ctx, ddns='pi')
        self.assertEqual(sections.statuses(env.ctx, ['example.com'])['ddns'].label, 'this Pi updates example.com')
        r.enabled.add(sections.LE_SITE)
        self.assertIn('renews over port 80', sections.statuses(env.ctx)['https'].label)
        self.assertEqual(sections.statuses(env.ctx)['allowlist'].label, 'off')


class WizardPartsTests(unittest.TestCase):
    def test_tunnel_section_keeps_the_allowlist_and_adds_the_tunnel_hostname(self):
        env = Env([('Public hostname', HOST)], config=fakes.FAKE_CONFIG.replace('?>', "$ALLOWED_HOSTS = array('raspberrypi.local');\n?>"))
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan(parts=('mode',))
        self.assertNotIn('Enable the hostname allowlist?', env.ui.out)
        self.assertEqual(w.new['ALLOWED_HOSTS'], [HOST, 'raspberrypi.local'])
        self.assertIn('Added %s to the hostname allowlist' % HOST, env.ui.text())

    def test_tunnel_section_leaves_a_disabled_allowlist_off(self):
        env = Env([('Public hostname', HOST)])
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan(parts=('mode',))
        self.assertEqual(w.new['ALLOWED_HOSTS'], [])

    def test_allowlist_section_in_tunnel_mode_needs_no_token_and_leaves_the_service_alone(self):
        env = Env([('Public hostname', HOST)])
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan(parts=('mode',))
        w.prepare_cloud()
        w.write_config()
        w.apply_local()
        sections.mark_done(env.ctx, 'remote')
        calls_before = len(env.cf.calls)
        runner_before = len(env.runner.calls)
        env.make_ctx([('Enable the hostname allowlist', 1)])
        env.ctx.env.pop('CLOUDFLARE_API_TOKEN', None)
        w2 = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w2.plan(parts=('allowlist',))
        self.assertNotIn('Public hostname for RWSOLS', ' '.join(env.ui.out))
        w2.write_config()
        w2.apply_local(service=False)
        self.assertEqual(len(env.cf.calls), calls_before)                       # Cloudflare was not contacted
        later = [' '.join(c['argv']) for c in env.runner.calls[runner_before:]]
        self.assertFalse([c for c in later if 'cloudflared' in c and 'which' not in c], later)
        self.assertIn(HOST, env.config())
        st = json.loads(env.state_text())
        self.assertTrue(st['sections']['remote'])                                # progress written meanwhile is kept
        self.assertEqual(st['mode'], 'tunnel')

    def test_lan_certificate_name_is_offered_for_the_allowlist(self):
        env = Env()
        self.addCleanup(env.cleanup)
        sections.load(env.ctx)
        sections.update(env.ctx, https={'kind': 'dns', 'names': ['pi.home.example.com']})
        w = wizard.RemoteAccessWizard(env.ctx, 'none', [], None)
        opts = dict((o.value, o) for o in w.allowlist_options())
        self.assertTrue(opts['pi.home.example.com'].checked)


class SetupScriptSectionTests(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup_py()
        self.addCleanup(setattr, self.setup, 'ui', None)
        self.addCleanup(setattr, self.setup, 'ctx', None)

    def wire(self, rules=(), runner=None):
        env = Env(list(rules), runner=runner)
        self.addCleanup(env.cleanup)
        self.setup.ui, self.setup.ctx = env.ui, env.ctx
        return env

    def test_every_section_has_a_runner(self):
        for key in sections.KEYS:
            self.assertTrue(callable(getattr(self.setup, 'section_' + key)), key)

    def test_dynamic_dns_is_optional_with_a_tunnel_and_can_be_declined(self):
        env = self.wire()
        sections.load(env.ctx)
        sections.update(env.ctx, mode='tunnel')
        self.assertTrue(self.setup.section_ddns())
        text = env.ui.text()
        self.assertIn('Do you need it?', text)
        self.assertIn('RWSOLS itself does not need dynamic DNS', text)
        self.assertEqual(json.loads(env.state_text())['ddns'], 'none')

    def test_dynamic_dns_on_this_pi_is_kept_by_default_with_a_tunnel(self):
        runner = FakeRunner()
        runner.files['/etc/ddclient.conf'] = (('protocol=cloudflare\nlogin=token\npassword=%s\nexample.com\n' % API_TOKEN).encode(), None)
        env = self.wire(runner=runner)
        sections.load(env.ctx)
        sections.update(env.ctx, mode='tunnel')
        self.setup._05_get_ip = lambda: True
        self.setup._06_check_urls = lambda: True
        self.assertTrue(self.setup.section_ddns())
        self.assertIn('Dynamic DNS is already set up on this Pi for: example.com', env.ui.text())
        self.assertIn('Turn it off on this Pi.', str(env.ui.choices))
        self.assertEqual(json.loads(env.state_text())['ddns'], 'pi')
        self.assertFalse([c for c in runner.mutations])

    def test_domain_found_on_cloudflare_selects_cloudflare_dynamic_dns_without_the_protocol_list(self):
        env = self.wire()
        sections.load(env.ctx)
        sections.update(env.ctx, domain={'name': 'example.com', 'provider': 'cloudflare'})
        self.assertEqual(wizard.choose_ddns_protocol(env.ui, self.setup.known_domain()), ('cloudflare', 'example.com'))
        text = env.ui.text()
        self.assertIn('DNS for example.com is managed by Cloudflare', text)
        self.assertIn('Using Cloudflare for example.com.', text)
        self.assertNotIn(wizard.DDNS_PROTOCOL_QUERY, str(env.ui.choices))

    def test_domain_of_a_tunnel_set_up_before_the_domain_was_recorded_is_looked_up(self):
        env = self.wire()
        sections.load(env.ctx)
        sections.update(env.ctx, mode='tunnel', tunnel={'hostname': HOST})
        self.assertEqual(self.setup.known_domain(), {'name': 'example.com', 'provider': 'cloudflare'})
        self.assertEqual(json.loads(env.state_text())['domain']['name'], 'example.com')

    def test_cloudflare_dynamic_dns_can_be_declined_for_another_domain_or_provider(self):
        env = self.wire([(wizard.DDNS_CLOUDFLARE_QUERY % 'example.com', False), (wizard.DDNS_PROTOCOL_QUERY, 2)])
        sections.load(env.ctx)
        sections.update(env.ctx, domain={'name': 'example.com', 'provider': 'cloudflare'})
        self.assertEqual(wizard.choose_ddns_protocol(env.ui, self.setup.known_domain()), ('noip', None))

    def test_domain_elsewhere_or_unknown_gets_the_full_protocol_list(self):
        env = self.wire([(wizard.DDNS_PROTOCOL_QUERY, 3)])
        sections.load(env.ctx)
        self.assertIsNone(self.setup.known_domain())
        self.assertEqual(wizard.choose_ddns_protocol(env.ui, None), ('cloudflare', None))      # nothing known: zone is asked later
        sections.update(env.ctx, domain={'name': 'example.net', 'provider': 'other'})
        self.assertEqual(wizard.choose_ddns_protocol(env.ui, self.setup.known_domain()), ('cloudflare', None))
        text = env.ui.text()
        self.assertIn('DNS for example.net is not managed by Cloudflare', text)
        self.assertNotIn(wizard.DDNS_CLOUDFLARE_QUERY % 'example.net', text)

    def test_port_forwarding_does_not_offer_to_skip_dynamic_dns(self):
        env = self.wire()
        sections.load(env.ctx)
        sections.update(env.ctx, mode='port_forward')
        self.setup._04_setup_ddns = lambda option=None: True
        self.setup._05_get_ip = lambda: True
        self.setup._06_check_urls = lambda: False
        self.assertFalse(self.setup.section_ddns())              # the check is fatal when DDNS is required
        self.assertNotIn('No dynamic DNS.', str(env.ui.choices))

    def test_lan_encryption_question_explains_the_tunnel_certificate_and_offers_the_dns_option(self):
        env = self.wire([('How should RWSOLS be served', 4)])
        sections.load(env.ctx)
        sections.update(env.ctx, mode='tunnel', tunnel={'hostname': HOST})
        self.assertTrue(self.setup._07_lan_encryption_check())
        text = env.ui.text()
        self.assertIn('https://%s, already has a certificate that every browser trusts' % HOST, text)
        self.assertIn('is not needed with a tunnel', text)
        self.assertIn('can never get a certificate that browsers trust', text)
        self.assertEqual(self.setup.encryption_mode, 'dns')

    def test_token_note_is_about_switching_later_and_absent_once_a_tunnel_is_chosen(self):
        self.assertIsNone(wizard.ddns_token_note('tunnel'))
        for mode in ('none', 'port_forward', None):
            title, lines = wizard.ddns_token_note(mode)
            self.assertIn('later', title)
            self.assertNotIn('Later in this setup', ' '.join(lines))
        url = wizard.ddns_token_url('example.com')
        self.assertIn('RWSOLS%20dynamic%20DNS%20%28example.com%29', url)
        self.assertNotIn('argotunnel', url)

    def test_tunnel_section_summary_leaves_the_allowlist_to_its_own_section(self):
        env = Env([('Public hostname', HOST)], config=fakes.FAKE_CONFIG.replace('?>', "$ALLOWED_HOSTS = array('raspberrypi.local');\n?>"))
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan(parts=('mode',))
        w.prepare_cloud()
        w.write_config()
        w.apply_local()
        text = env.ui.text()
        self.assertNotIn('Allowed hosts:', text)
        self.assertNotIn('TLS note', text)
        self.assertIn('You cannot log in yet: no passphrase is set.', text)
        self.assertNotIn('Remove any router port forwards for RWSOLS.', text.split('Remote access summary')[0])

    def test_config_editor_is_the_last_section(self):
        self.assertEqual(sections.KEYS[-1], 'config')
        with open(os.path.join(fakes.ROOT, 'setup.py'), newline='') as f:
            src = f.read()
        body = src.split('def section_config():')[1].split('\r\n\r\n')[0]
        self.assertIn('_11_modify_config_file', body)
        self.assertEqual(src.count("subprocess.run(['nano', str(rwsols_config_user)]"), 1)


if __name__ == '__main__':
    unittest.main()
