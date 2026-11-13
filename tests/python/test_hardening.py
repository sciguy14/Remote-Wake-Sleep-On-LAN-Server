import unittest

from fakes import FakeRunner, ScriptedUI

from rwsols_setup import hardening
from rwsols_setup.errors import CommandError, SetupError


class AutoUpdatesTests(unittest.TestCase):
    def test_status_off_on_a_fresh_system(self):
        self.assertEqual(hardening.auto_updates_status(FakeRunner()),
                         {'installed': False, 'enabled': False, 'auto_reboot': False})

    def test_older_config_gains_cloudflared_and_keeps_the_reboot_choice(self):
        runner = FakeRunner()
        ui = ScriptedUI([('Install security updates automatically', True), ('Restart automatically', True)])
        hardening.run_auto_updates_step(runner, ui)
        current = runner.files[hardening.EXTRA_PATH][0].decode()
        old = ''.join(l + chr(10) for l in current.splitlines() if 'cloudflared' not in l)
        runner.files[hardening.EXTRA_PATH] = (old.encode(), '0644')
        ui2 = ScriptedUI()
        self.assertEqual(hardening.run_auto_updates_step(runner, ui2), 'already')
        self.assertEqual(runner.files[hardening.EXTRA_PATH][0].decode(), current)
        self.assertIn('Automatic-Reboot "true"', current)
        self.assertIn('Updated the list of package sources', ui2.text())
        self.assertFalse(hardening.refresh_extra_conf(runner))          # nothing to do the second time
        self.assertFalse(hardening.refresh_extra_conf(FakeRunner()))    # never creates the file by itself

    def test_installed_but_not_switched_on_is_not_enabled(self):
        runner = FakeRunner()
        runner.packages.add('unattended-upgrades')
        self.assertFalse(hardening.auto_updates_status(runner)['enabled'])
        runner.files[hardening.PERIODIC_PATH] = (b'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "0";\n', None)
        self.assertFalse(hardening.auto_updates_status(runner)['enabled'])

    def test_enable_installs_writes_both_files_and_starts_timers(self):
        runner = FakeRunner()
        ui = ScriptedUI([('Install security updates automatically', True), ('Restart automatically', False)])
        self.assertEqual(hardening.run_auto_updates_step(runner, ui), 'enabled')
        self.assertIn(['env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', 'unattended-upgrades'], runner.argvs())
        self.assertEqual(runner.files[hardening.PERIODIC_PATH][0].decode(), hardening.PERIODIC_CONF)
        extra = runner.files[hardening.EXTRA_PATH][0].decode()
        self.assertIn('origin=Raspberry Pi Foundation,codename=${distro_codename}', extra)
        self.assertIn('origin=Raspbian,codename=${distro_codename}', extra)
        self.assertIn('"origin=cloudflared,label=cloudflared";', extra)
        self.assertNotIn('Automatic-Reboot', extra)
        for timer in hardening.TIMERS:
            self.assertIn(['systemctl', 'enable', '--now', timer], runner.argvs())
        self.assertEqual(hardening.auto_updates_status(runner),
                         {'installed': True, 'enabled': True, 'auto_reboot': False})

    def test_automatic_restart_is_opt_in(self):
        runner = FakeRunner()
        ui = ScriptedUI([('Install security updates automatically', True), ('Restart automatically', True)])
        hardening.run_auto_updates_step(runner, ui)
        extra = runner.files[hardening.EXTRA_PATH][0].decode()
        self.assertIn('Unattended-Upgrade::Automatic-Reboot "true";', extra)
        self.assertIn('Unattended-Upgrade::Automatic-Reboot-Time "04:00";', extra)
        self.assertTrue(hardening.auto_updates_status(runner)['auto_reboot'])

    def test_declining_changes_nothing(self):
        runner = FakeRunner()
        ui = ScriptedUI([('Install security updates automatically', False)])
        self.assertEqual(hardening.run_auto_updates_step(runner, ui), 'declined')
        self.assertEqual(runner.mutations, [])
        self.assertIn('sudo apt update', ui.text())

    def test_already_enabled_asks_nothing_and_changes_nothing(self):
        runner = FakeRunner()
        runner.packages.add('unattended-upgrades')
        runner.files[hardening.PERIODIC_PATH] = (hardening.PERIODIC_CONF.encode(), None)
        ui = ScriptedUI([])
        self.assertEqual(hardening.run_auto_updates_step(runner, ui), 'already')
        self.assertEqual(runner.mutations, [])

    def test_failed_install_raises_and_writes_no_config(self):
        runner = FakeRunner()
        runner.apt_ok = False
        ui = ScriptedUI([('Install security updates automatically', True), ('Restart automatically', False)])
        with self.assertRaises((SetupError, CommandError)):
            hardening.run_auto_updates_step(runner, ui)
        self.assertNotIn(hardening.PERIODIC_PATH, runner.files)


class WebrootPermissionTests(unittest.TestCase):
    CONFIG = '/var/www/html/config.php'

    def test_config_is_restricted_to_root_and_web_server(self):
        runner = FakeRunner()
        runner.files[self.CONFIG] = (b'<?php', None)
        done = hardening.secure_webroot(runner)
        argvs = runner.argvs()
        self.assertLess(argvs.index(['chown', 'root:www-data', self.CONFIG]), argvs.index(['chmod', '640', self.CONFIG]))
        self.assertEqual(len(done), 1)

    def test_already_restricted_config_is_left_alone(self):
        runner = FakeRunner()
        runner.files[self.CONFIG] = (b'<?php', None)
        runner.stat_out = 'root:www-data 640'
        self.assertEqual(hardening.secure_webroot(runner), [])
        self.assertEqual(runner.mutations, [])

    def test_backups_are_closed_to_other_users(self):
        runner = FakeRunner()
        runner.files[self.CONFIG] = (b'<?php', None)
        runner.found = ['/var/www/html_backup_2024-01-01_10-00', '/var/www/html_backup_2024-02-01_10-00', '/etc/passwd']
        done = hardening.secure_webroot(runner)
        self.assertIn(['chmod', '700', '/var/www/html_backup_2024-01-01_10-00'], runner.argvs())
        self.assertIn(['chmod', '700', '/var/www/html_backup_2024-02-01_10-00'], runner.argvs())
        self.assertIn(['chown', 'root:root', '/var/www/html_backup_2024-02-01_10-00'], runner.argvs())
        self.assertNotIn(['chmod', '700', '/etc/passwd'], runner.argvs())
        self.assertIn('2 web root backup folder(s)', done[1])

    def test_nothing_happens_without_the_web_server_group_or_config(self):
        runner = FakeRunner()
        runner.files[self.CONFIG] = (b'<?php', None)
        runner.groups = set()
        self.assertEqual(hardening.secure_webroot(runner), [])
        self.assertEqual(runner.mutations, [])
        runner = FakeRunner()
        self.assertEqual(hardening.secure_webroot(runner), [])
        self.assertEqual(runner.mutations, [])


if __name__ == '__main__':
    unittest.main()
