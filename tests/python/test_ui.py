import importlib.util
import io
import os
import sys
import unittest
import warnings
from unittest import mock

import fakes
from fakes import Env, FakeResolver, FakeRunner

from rwsols_setup import ui as uimod
from rwsols_setup import wizard
from rwsols_setup.hosts import validate_hostname
from rwsols_setup.runtime import SystemResolver
from rwsols_setup.ui import Option, PlainUI

LAN_IP = '192.168.1.10'


def plain(answers, secrets=()):
    """PlainUI fed from a list of lines; returns (ui, output stream)."""
    lines, secs, out = list(answers), list(secrets), io.StringIO()

    def read():
        if not lines:
            raise EOFError
        return lines.pop(0)

    return PlainUI(input_fn=read, secret_fn=lambda prompt: secs.pop(0), out=out, color=False), out


class TTY(io.StringIO):
    def isatty(self):
        return True


class ChooseManyTests(unittest.TestCase):
    OPTS = [Option('wol.example.com', 'tunnel', True, True), Option('raspberrypi.local', 'mdns', True),
            Option('192.168.1.10', 'lan ip', False), Option('home.example.com', 'ddns', False)]

    def run_many(self, answers, **kw):
        ui, out = plain(answers)
        return ui.choose_many('Pick', self.OPTS, validator=validate_hostname, **kw), out.getvalue()

    def test_enter_accepts_defaults_in_display_order(self):
        got, text = self.run_many([''])
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local'])
        self.assertIn('[x]', text)
        self.assertIn('[ ]', text)
        self.assertIn('required: tunnel', text)

    def test_toggle_with_spaces_and_commas_and_rerender(self):
        got, text = self.run_many(['3, 4', '2', ''])
        self.assertEqual(got, ['wol.example.com', '192.168.1.10', 'home.example.com'])
        self.assertEqual(text.count('Why'), 3)   # table redrawn after each change

    def test_toggle_twice_restores(self):
        got, _ = self.run_many(['3', '3', ''])
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local'])

    def test_locked_item_cannot_be_unticked(self):
        got, text = self.run_many(['1', ''])
        self.assertIn('wol.example.com', got)
        self.assertIn('required and cannot be unticked', text)

    def test_add_custom_entry_validated_and_marked_added(self):
        got, text = self.run_many(['a', 'bad name!', 'Nas.Example.com', ''])
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local', 'nas.example.com'])
        self.assertIn('invalid hostname', text)
        self.assertIn('added by you', text)

    def test_add_existing_name_just_ticks_it(self):
        got, _ = self.run_many(['a', 'HOME.example.com', ''])
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local', 'home.example.com'])

    def test_add_can_be_cancelled(self):
        got, _ = self.run_many(['a', '', ''])
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local'])

    def test_at_least_one_must_stay_selected(self):
        opts = [Option('a.example.com', 'x', True), Option('b.example.com', 'y', False)]
        ui, out = plain(['1', '', '2', ''])
        self.assertEqual(ui.choose_many('Pick', opts), ['b.example.com'])
        self.assertIn('Select at least 1 entry', out.getvalue())

    def test_garbage_input_is_reported(self):
        got, text = self.run_many(['9 zz', ''])
        self.assertIn('Not understood: 9, zz', text)
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local'])


class PlainUITests(unittest.TestCase):
    def piped(self, text):
        """Drive PlainUI through the real input() with a piped (non-TTY) stdin."""
        out = io.StringIO()
        patches = [mock.patch('sys.stdin', io.StringIO(text)), mock.patch('sys.stdout', out)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return PlainUI(secret_fn=lambda prompt: 'secret'), out

    def test_choose_default_and_explicit_and_reask(self):
        ui, out = self.piped('\n3\nx\n9\n2\n')
        self.assertEqual(ui.choose('Q', ['one', 'two', 'three'], 2), (2, 'two'))
        self.assertEqual(ui.choose('Q', ['one', 'two', 'three']), (3, 'three'))
        self.assertEqual(ui.choose('Q', ['one', 'two', 'three']), (2, 'two'))
        self.assertEqual(out.getvalue().count('Please choose a number from the list!'), 2)
        self.assertIn('(default)', out.getvalue())

    def test_choose_without_default_rejects_blank(self):
        ui, out = self.piped('\n1\n')
        self.assertEqual(ui.choose('Q', ['one', 'two']), (1, 'one'))
        self.assertIn('Please choose', out.getvalue())

    def test_confirm(self):
        ui, _ = self.piped('\n\ny\nNo\nmaybe\nyes\n')
        self.assertTrue(ui.confirm('Q', True))
        self.assertFalse(ui.confirm('Q', False))
        self.assertTrue(ui.confirm('Q', False))
        self.assertFalse(ui.confirm('Q', True))
        self.assertTrue(ui.confirm('Q', False))

    def test_ask_default_and_validator_reask(self):
        ui, out = self.piped('\nnot a host!\nOK.example.com\n')
        self.assertEqual(ui.ask('Name', 'dflt.example.com'), 'dflt.example.com')
        self.assertEqual(ui.ask('Name', None, validator=validate_hostname), 'ok.example.com')
        self.assertIn('invalid hostname', out.getvalue())
        self.assertIn('[dflt.example.com]', out.getvalue())

    def test_validator_applies_to_default(self):
        ui, out = self.piped('\n\nfine.example.com\n')
        self.assertEqual(ui.ask('Name', 'bad name', validator=validate_hostname), 'fine.example.com')

    def test_ask_secret_strip_option(self):
        ui = PlainUI(secret_fn=lambda p: '  pw  ', out=io.StringIO(), color=False)
        self.assertEqual(ui.ask_secret('x'), 'pw')
        self.assertEqual(ui.ask_secret('x', strip=False), '  pw  ')

    def test_eof_aborts_cleanly(self):
        ui, out = plain([])
        with self.assertRaises(SystemExit):
            ui.ask('Name')
        self.assertIn('Input ended unexpectedly', out.getvalue())

    def test_no_ansi_when_not_a_tty(self):
        ui, out = self.piped('')
        ui.warn('careful')
        ui.explain('T', ['line'])
        self.assertNotIn('\033', out.getvalue())

    def test_boxes_tables_and_checklist_render(self):
        ui, out = plain([])
        ui.heading('Section')
        ui.explain('Why', ['a short line', '    indented line'])
        ui.summary('Done', ['all good'], ['careful'])
        ui.checklist('Checks', [('one thing', True, ''), ('other thing', False, 'HTTP 502')])
        text = out.getvalue()
        for want in ('Section', 'Why', 'a short line', 'Done', 'careful', '[ok]   one thing', '[FAIL] other thing (HTTP 502)'):
            self.assertIn(want, text)


class SelectionTests(unittest.TestCase):
    def test_plain_when_rich_not_importable(self):
        def no_rich():
            raise ImportError('no rich')
        got = uimod.select_ui(stdin=TTY(), stdout=TTY(), importer=no_rich)
        self.assertIsInstance(got, PlainUI)

    def test_plain_when_not_a_tty(self):
        got = uimod.select_ui(stdin=io.StringIO(), stdout=TTY(), importer=lambda: None)
        self.assertIsInstance(got, PlainUI)
        got = uimod.select_ui(stdin=TTY(), stdout=io.StringIO(), importer=lambda: None)
        self.assertIsInstance(got, PlainUI)

    def test_rich_only_with_rich_and_tty(self):
        if not uimod.rich_available():
            self.skipTest('rich is not installed')
        self.assertIsInstance(uimod.select_ui(stdin=TTY(), stdout=TTY()), uimod.RichUI)


class BootstrapTests(unittest.TestCase):
    class Runner(object):
        def __init__(self, candidate='13.3.1-1'):
            self.calls = []
            self.candidate = candidate

        def __call__(self, argv, **kw):
            self.calls.append(list(argv))
            out = 'python3-rich:\n  Installed: (none)\n  Candidate: %s\n' % self.candidate if argv[0] == 'apt-cache' else ''
            return mock.Mock(returncode=0, stdout=out)

    def boot(self, runner, **kw):
        def no_rich():
            raise ImportError
        args = dict(importer=no_rich, run=runner, which=lambda n: '/usr/bin/' + n, say=lambda t: said.append(t),
                    stdin=TTY(), stdout=TTY())
        args.update(kw)
        said = []
        res = uimod.bootstrap_rich(**args)
        return res, said

    def test_installs_with_apt_when_candidate_exists(self):
        r = self.Runner()
        res, said = self.boot(r)
        self.assertTrue(res)
        self.assertEqual(r.calls, [['apt-cache', 'policy', 'python3-rich'], ['sudo', 'apt-get', 'install', '-y', 'python3-rich']])
        self.assertEqual(said, ['Installing python3-rich for a nicer setup experience...'])

    def test_skipped_for_dry_run(self):
        r = self.Runner()
        res, said = self.boot(r, skip=True)
        self.assertFalse(res)
        self.assertEqual((r.calls, said), ([], []))

    def test_skipped_when_rich_present_or_not_interactive_or_no_apt(self):
        for kw in (dict(importer=lambda: None), dict(stdin=io.StringIO()), dict(which=lambda n: None)):
            r = self.Runner()
            res, said = self.boot(r, **kw)
            self.assertEqual((res, r.calls, said), (False, [], []), kw)

    def test_no_candidate_means_no_install(self):
        r = self.Runner('(none)')
        res, said = self.boot(r)
        self.assertEqual((res, said), (False, []))
        self.assertEqual(len(r.calls), 1)

    def test_failures_are_silent(self):
        def boom(argv, **kw):
            raise OSError('nope')
        res, said = self.boot(boom)
        self.assertEqual((res, said), (False, []))

    def test_setup_py_skips_bootstrap_for_dry_run(self):
        setup = load_setup_py()
        with mock.patch.object(uimod, 'bootstrap_rich') as boot, mock.patch.object(sys, 'argv', ['setup.py', '--remote-access', '--dry-run']), \
                mock.patch.object(uimod, 'select_ui', side_effect=RuntimeError('stop here')):
            with self.assertRaises(RuntimeError):
                setup.main()
        boot.assert_called_once_with(skip=True)


class ResolverTests(unittest.TestCase):
    def test_never_raises(self):
        r = SystemResolver(timeout=0.5)
        with mock.patch('socket.gethostbyaddr', side_effect=OSError('x')), mock.patch('socket.gethostbyname_ex', side_effect=OSError('x')):
            self.assertIsNone(r.reverse(LAN_IP))
            self.assertEqual(r.forward('pi.example.com'), [])

    def test_timeout_guard(self):
        import time
        r = SystemResolver(timeout=0.05)
        with mock.patch('socket.gethostbyaddr', side_effect=lambda ip: time.sleep(1) or ('x', [], [ip])):
            t = time.time()
            self.assertIsNone(r.reverse(LAN_IP))
            self.assertLess(time.time() - t, 0.9)

    def test_results_passed_through(self):
        r = SystemResolver()
        with mock.patch('socket.gethostbyaddr', return_value=('pi.lan.example.net', [], [LAN_IP])), \
                mock.patch('socket.gethostbyname_ex', return_value=('pi.lan.example.net', [], [LAN_IP])):
            self.assertEqual(r.reverse(LAN_IP), 'pi.lan.example.net')
            self.assertEqual(r.forward('pi.lan.example.net'), [LAN_IP])


def options_of(env, mode='none', suggested=(), lan_ip=LAN_IP):
    w = wizard.RemoteAccessWizard(env.ctx, mode, suggested, lan_ip)
    return w, dict((o.value, o) for o in w.allowlist_options())


class AllowlistOptionTests(unittest.TestCase):
    def env(self, reverse=None, forward=None, **kw):
        env = Env(resolver=FakeResolver(reverse, forward), **kw)
        self.addCleanup(env.cleanup)
        return env

    def test_reverse_dns_offered_only_when_forward_confirmed(self):
        env = self.env({LAN_IP: 'pi.lan.example.net'}, {'pi.lan.example.net': [LAN_IP, '192.168.0.99']})
        _, opts = options_of(env)
        self.assertTrue(opts['pi.lan.example.net'].checked)
        self.assertIn('reverse lookup of ' + LAN_IP, opts['pi.lan.example.net'].why)
        self.assertIn("router's local DNS name", opts['pi.lan.example.net'].why)

    def test_reverse_dns_absent_when_lookup_fails_or_mismatches(self):
        for reverse, forward in (({}, {}),
                                 ({LAN_IP: 'pi.lan.example.net'}, {}),
                                 ({LAN_IP: 'pi.lan.example.net'}, {'pi.lan.example.net': ['192.168.0.77']}),
                                 ({LAN_IP: 'localhost'}, {'localhost': [LAN_IP]}),
                                 ({LAN_IP: 'not a name!'}, {}),
                                 ({LAN_IP: '192.168.1.10'}, {'192.168.1.10': [LAN_IP]})):
            env = self.env(reverse, forward)
            _, opts = options_of(env)
            self.assertEqual(sorted(opts), sorted(['raspberrypi.local', LAN_IP]), (reverse, forward))

    def test_search_domain_name_offered_only_when_it_resolves_to_this_pi(self):
        env = self.env({}, {'raspberrypi.home.example.net': [LAN_IP], 'raspberrypi.other.example.net': ['192.168.0.77']})
        env.runner.files['/etc/resolv.conf'] = (b'# c\nnameserver 192.168.0.1\nsearch home.example.net other.example.net missing.example.net\n', None)
        _, opts = options_of(env)
        self.assertTrue(opts['raspberrypi.home.example.net'].checked)
        self.assertIn('search domain', opts['raspberrypi.home.example.net'].why)
        self.assertNotIn('raspberrypi.other.example.net', opts)
        self.assertNotIn('raspberrypi.missing.example.net', opts)

    def test_reverse_dns_same_as_local_name_not_duplicated(self):
        env = self.env({LAN_IP: 'raspberrypi.local'}, {'raspberrypi.local': [LAN_IP]})
        w, opts = options_of(env)
        self.assertEqual([o.value for o in w.allowlist_options()].count('raspberrypi.local'), 1)

    def test_resolver_that_raises_is_tolerated(self):
        class Bad(object):
            def reverse(self, ip):
                raise RuntimeError('boom')

            def forward(self, name):
                raise RuntimeError('boom')
        env = self.env()
        env.ctx.resolver = Bad()
        _, opts = options_of(env)
        self.assertIn('raspberrypi.local', opts)

    def test_lan_ip_unticked_by_default_ticked_when_saved(self):
        env = self.env()
        _, opts = options_of(env)
        self.assertFalse(opts[LAN_IP].checked)
        self.assertIn('reserve the address in your router', opts[LAN_IP].why)
        config = fakes.FAKE_CONFIG.replace('?>', "$ALLOWED_HOSTS = array('%s');\n?>" % LAN_IP)
        env2 = self.env(config=config)
        _, opts = options_of(env2)
        self.assertTrue(opts[LAN_IP].checked)

    def test_no_lan_ip_means_no_ip_option(self):
        env = self.env()
        _, opts = options_of(env, lan_ip=None)
        self.assertEqual(list(opts), ['raspberrypi.local'])

    def test_existing_names_ticked_with_reason(self):
        config = fakes.FAKE_CONFIG.replace('?>', "$ALLOWED_HOSTS = array('old.example.com');\n?>")
        env = self.env(config=config)
        _, opts = options_of(env)
        self.assertTrue(opts['old.example.com'].checked)
        self.assertEqual(opts['old.example.com'].why, 'already on your list')

    def ddclient_env(self, config=None):
        runner = FakeRunner()
        runner.files['/etc/ddclient.conf'] = (b'protocol=dyndns2\nlogin=u\npassword=p\nhome.example.com\n', None)
        return self.env(runner=runner, config=config or fakes.FAKE_CONFIG)

    def test_ddclient_name_ticked_in_port_forwarding_unticked_in_tunnel_and_lan(self):
        saved = fakes.FAKE_CONFIG.replace('?>', "$ALLOWED_HOSTS = array('home.example.com');\n?>")
        _, opts = options_of(self.ddclient_env(saved), 'port_forward')
        self.assertTrue(opts['home.example.com'].checked)
        self.assertIn('port forwards', opts['home.example.com'].why)
        for mode in ('tunnel', 'none'):
            env = self.ddclient_env(saved)
            w = wizard.RemoteAccessWizard(env.ctx, mode, [], LAN_IP)
            w.new['TUNNEL_HOSTNAME'] = 'wol.example.com'
            opts = dict((o.value, o) for o in w.allowlist_options())
            self.assertFalse(opts['home.example.com'].checked, mode)
            self.assertIn('only needed while router port forwards still point at this Pi', opts['home.example.com'].why)

    def test_tunnel_hostname_is_locked_and_first(self):
        env = self.env()
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], LAN_IP)
        w.new['TUNNEL_HOSTNAME'] = 'wol.example.com'
        first = w.allowlist_options()[0]
        self.assertEqual((first.value, first.checked, first.locked), ('wol.example.com', True, True))
        self.assertEqual(first.why, 'your Cloudflare Tunnel hostname (required in tunnel mode)')

    def test_full_run_through_plain_ui_with_piped_answers(self):
        env = self.env({LAN_IP: 'pi.lan.example.net'}, {'pi.lan.example.net': [LAN_IP]})
        ui, out = plain(['2', '1', '', '', ''])
        env.ctx.ui = ui
        wizard.run_remote_access(env.ctx, [], LAN_IP)
        self.assertIn("$ALLOWED_HOSTS = array('raspberrypi.local', 'pi.lan.example.net');", env.config())
        self.assertIn('lookup of ' + LAN_IP, out.getvalue())


class TunnelHostnameDefaultTests(unittest.TestCase):
    def test_default_is_rwsols_subdomain_of_first_ddclient_name(self):
        runner = FakeRunner()
        runner.files['/etc/ddclient.conf'] = (b'protocol=dyndns2\nlogin=u\npassword=p\nhome.example.com, other.example.com\n', None)
        env = Env(runner=runner)
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        self.assertEqual(w.new['TUNNEL_HOSTNAME'], 'rwsols.home.example.com')
        text = env.ui.text()
        self.assertIn('Dynamic DNS keeps updating home.example.com, other.example.com to your home IP exactly as before', text)
        self.assertIn('the two coexist', text)

    def test_previous_hostname_wins_and_no_ddclient_means_no_default(self):
        env = Env([('Public hostname', 'wol.example.com')])
        self.addCleanup(env.cleanup)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        self.assertNotIn('Dynamic DNS keeps updating', env.ui.text())
        env2 = Env(config=fakes.FAKE_CONFIG.replace('?>', "$TUNNEL_HOSTNAME = 'prev.example.com';\n?>"))
        self.addCleanup(env2.cleanup)
        runner = env2.runner
        runner.files['/etc/ddclient.conf'] = (b'protocol=x\nhome.example.com\n', None)
        w = wizard.RemoteAccessWizard(env2.ctx, 'tunnel', [], None)
        w.plan()
        self.assertEqual(w.new['TUNNEL_HOSTNAME'], 'prev.example.com')

    def test_subdomain_explanation_shown(self):
        env = Env([('Public hostname', 'wol.example.com')])
        self.addCleanup(env.cleanup)
        wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None).plan()
        text = env.ui.text()
        self.assertIn('nas.example.com', text)
        self.assertIn('proxied CNAME', text)


def load_setup_py():
    path = os.path.join(fakes.ROOT, 'setup.py')
    spec = importlib.util.spec_from_file_location('rwsols_setup_script', path)
    mod = importlib.util.module_from_spec(spec)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        spec.loader.exec_module(mod)
    return mod


class SetupPyTests(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup_py()
        self.addCleanup(setattr, self.setup, 'ui', None)

    def test_uses_no_raw_prompts(self):
        with open(os.path.join(fakes.ROOT, 'setup.py'), newline='') as f:
            src = f.read()
        self.assertIn('\r\n', src)            # CRLF preserved
        self.assertNotIn('\n', src.replace('\r\n', ''))
        for banned in (' input(', '\tinput(', 'getpass', 'multi_choice', "print('\\n", 'yellow(', 'cyan('):
            self.assertNotIn(banned, src, banned)

    def test_enter_urls_reasks_through_ui(self):
        self.setup.ui, out = plain(['', 'bad host!', 'a.example.com, B.example.com'])
        self.assertEqual(self.setup.enter_urls('Enter hosts'), ['a.example.com', 'B.example.com'])
        self.assertIn('A hostname is required', out.getvalue())
        self.assertIn("'bad host!' is an invalid hostname.", out.getvalue())

    def test_lan_encryption_check_through_ui(self):
        self.setup.ui, out = plain(['not valid!', 'pi.local', '2'])
        self.assertTrue(self.setup._07_lan_encryption_check())
        self.assertEqual(self.setup.urls, ['pi.local'])
        self.assertEqual(self.setup.encryption_mode, 'self')
        self.assertIn('self-signed', out.getvalue().lower())

    def test_run_step_skips_existing_dot_file(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, d, True)
        import pathlib
        self.setup.script_dir = pathlib.Path(d)
        open(os.path.join(d, '._99_demo'), 'w').close()
        self.setup.ui, out = plain([])
        self.setup.run_step('_99_demo', 'Demo step')
        self.assertIn("'Demo step' has already been completed. Skipping.", out.getvalue())


@unittest.skipUnless(uimod.rich_available(), 'rich is not installed')
class RichSmokeTests(unittest.TestCase):
    def make(self, answers=()):
        from rich.console import Console
        out = io.StringIO()
        ui = uimod.RichUI(Console(file=out, force_terminal=False, width=100, highlight=False))
        lines = list(answers)
        ui.console.input = lambda prompt='', **kw: lines.pop(0)
        return ui, out

    def test_renders_everything(self):
        ui, out = self.make(['', '', '2', 'y', ''])
        ui.heading('Section')
        ui.explain('Why', ['line [not markup]'])
        ui.summary('Done', ['ok'], ['careful'])
        ui.checklist('Checks', [('a', True, ''), ('b', False, 'HTTP 502')])
        self.assertEqual(ui.ask('Name', 'dflt'), 'dflt')
        self.assertEqual(ui.choose('Q', ['one', 'two'], 1), (1, 'one'))  # '' -> default
        self.assertEqual(ui.choose('Q', ['one', 'two']), (2, 'two'))
        self.assertTrue(ui.confirm('Sure?'))
        self.assertEqual(ui.choose_many('Pick', [Option('a.example.com', 'why a', True), Option('b.example.com', 'why b', False)]),
                         ['a.example.com'])
        text = out.getvalue()
        for want in ('Section', 'Why', '[not markup]', 'careful', 'HTTP 502', 'why a', '[x]', '[ ]'):
            self.assertIn(want, text)


class ScriptedKeys(object):
    """Stands in for ui.TtyKeys: hands out a fixed list of key names."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.entered -= 1
        return False

    def read(self):
        if not self.keys:
            raise EOFError
        return self.keys.pop(0)


@unittest.skipUnless(uimod.rich_available(), 'rich is not installed')
class RichKeyTests(unittest.TestCase):
    OPTS = [Option('wol.example.com', 'tunnel', True, True), Option('raspberrypi.local', 'mdns', True),
            Option('192.168.1.10', 'lan ip', False), Option('home.example.com', 'ddns', False)]

    def make(self, keys, answers=(), height=40):
        from rich.console import Console
        out = io.StringIO()
        self.keys = ScriptedKeys(keys)
        ui = uimod.RichUI(Console(file=out, force_terminal=False, width=100, height=height, highlight=False), keys=self.keys)
        lines = list(answers)
        ui.console.input = lambda prompt='', **kw: lines.pop(0)
        return ui, out

    def test_choose_moves_with_arrows_and_wraps(self):
        ui, out = self.make(['down', 'down', 'enter'])
        self.assertEqual(ui.choose('Q', ['one', 'two', 'three'], 1), (3, 'three'))
        ui, out = self.make(['up', 'enter'])
        self.assertEqual(ui.choose('Q', ['one', 'two', 'three'], 1), (3, 'three'))
        self.assertIn('three', out.getvalue())
        self.assertEqual(self.keys.entered, 0)      # terminal mode restored

    def test_choose_enter_takes_default_and_digit_jumps(self):
        ui, _ = self.make(['enter'])
        self.assertEqual(ui.choose('Q', ['one', 'two'], 2), (2, 'two'))
        ui, _ = self.make(['2', 'enter'])
        self.assertEqual(ui.choose('Q', ['one', 'two']), (2, 'two'))
        ui, _ = self.make(['9', None, 'x', 'enter'])  # out of range / unknown keys do nothing
        self.assertEqual(ui.choose('Q', ['one', 'two']), (1, 'one'))

    def test_choose_many_space_toggles_row_under_cursor(self):
        ui, out = self.make(['down', 'space', 'down', 'space', 'enter'])
        self.assertEqual(ui.choose_many('Pick', self.OPTS), ['wol.example.com', '192.168.1.10'])
        text = out.getvalue()
        self.assertIn('lan ip', text)
        self.assertIn('[x]', text)

    def test_choose_many_locked_row_cannot_be_unticked(self):
        ui, out = self.make(['space', '1', 'enter'])
        self.assertEqual(ui.choose_many('Pick', self.OPTS), ['wol.example.com', 'raspberrypi.local'])

    def test_choose_many_requires_min_selected(self):
        opts = [Option('a.example.com', 'a', True), Option('b.example.com', 'b', False)]
        ui, _ = self.make(['space', 'enter', 'down', 'space', 'enter'])
        self.assertEqual(ui.choose_many('Pick', opts), ['b.example.com'])

    def test_choose_many_add_validates_and_ticks(self):
        ui, _ = self.make(['a', 'a', 'enter'], answers=['not valid!', 'NAS.example.com', ''])
        got = ui.choose_many('Pick', self.OPTS, validator=validate_hostname)
        self.assertEqual(got, ['wol.example.com', 'raspberrypi.local', 'nas.example.com'])

    def test_end_of_input_aborts(self):
        ui, _ = self.make(['down'])
        with self.assertRaises(SystemExit):
            ui.choose('Q', ['one', 'two'])
        self.assertEqual(self.keys.entered, 0)

    def test_falls_back_to_numbers_when_list_does_not_fit_or_keys_break(self):
        ui, _ = self.make(['enter'], answers=['2'], height=5)
        self.assertEqual(ui.choose('Q', ['one', 'two', 'three']), (2, 'two'))
        self.assertEqual(self.keys.keys, ['enter'])  # key reader untouched

        class Broken(ScriptedKeys):
            def read(self):
                raise OSError('not a terminal')
        ui, out = self.make([], answers=['2', '3', ''])
        ui._keys = Broken([])
        self.assertEqual(ui.choose('Q', ['one', 'two']), (2, 'two'))
        self.assertIsNone(ui._keys)
        self.assertEqual(ui.choose_many('Pick', self.OPTS), ['wol.example.com', 'raspberrypi.local', '192.168.1.10'])
        self.assertIn('numbered choices', out.getvalue())

    def test_no_key_reader_without_a_terminal(self):
        from rich.console import Console
        ui = uimod.RichUI(Console(file=io.StringIO(), force_terminal=False))
        self.assertIsNone(ui._keys)


class TtyKeysTests(unittest.TestCase):
    def read_all(self, data):
        try:
            import pty
        except ImportError:
            self.skipTest('no pty on this platform')
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)

        class Stream(object):
            def fileno(self):
                return slave
        got = []
        with uimod.TtyKeys(Stream()) as keys:
            os.write(master, data)
            import time
            time.sleep(0.2)
            for _ in range(20):
                try:
                    k = keys.read()
                except EOFError:
                    break
                got.append(k)
                if k == 'enter':
                    break
        return got

    def test_decodes_arrows_space_letters_and_enter(self):
        self.assertEqual(self.read_all(b'\x1b[A\x1b[B\x1bOA\x1bOB a2X\x1b[C\x1b[3~\r'),
                         ['up', 'down', 'up', 'down', 'space', 'a', '2', 'x', None, None, 'enter'])


if __name__ == '__main__':
    unittest.main()
