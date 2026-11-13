import os
import shutil
import tempfile
import unittest

import fakes
from fakes import ScriptedUI

from rwsols_setup import computers, phpconfig


def sample_text():
    with open(os.path.join(fakes.ROOT, 'www', 'html', 'config_sample.php'), newline='') as f:
        return f.read()


class ComputersTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, 'config.php')
        with open(self.path, 'w', newline='') as f:
            f.write(sample_text())
        self.config = phpconfig.ConfigFile(self.path)

    def run_with(self, rules):
        ui = ScriptedUI(rules)
        result = computers.run(ui, self.config)
        return result, '\n'.join(str(x) for x in ui.out)

    def get(self, key):
        return self.config.get(key)

    def test_validators(self):
        self.assertEqual(computers.mac_address('01-23-45-67-89-ab'), '01:23:45:67:89:AB')
        self.assertEqual(computers.mac_address('0123.4567.89ab'), '01:23:45:67:89:AB')
        self.assertEqual(computers.ipv4_address(' 192.168.001.20 '), '192.168.1.20')
        for bad in ('', '01:23:45:67:89', 'zz:23:45:67:89:ab', '00:00:00:00:00:00'):
            self.assertRaises(ValueError, computers.mac_address, bad)
        for bad in ('', '192.168.0', '192.168.0.256', 'example.com', '1.2.3.4.5'):
            self.assertRaises(ValueError, computers.ipv4_address, bad)
        for bad in ('', 'a' * 41, '<b>pc</b>', "pc'; rm", 'pc"'):
            self.assertRaises(ValueError, computers.computer_name, bad)

    def test_sample_placeholders_do_not_count_as_computers(self):
        self.assertEqual(computers.read_computers(sample_text()), [])

    def test_two_computers_end_up_in_the_file_and_nothing_else_changes(self):
        answers = {'more': [True, False]}

        class UI(ScriptedUI):
            def confirm(self, prompt, default=False):
                if prompt == computers.ADD_ANOTHER:
                    self.out.append(prompt)
                    return answers['more'].pop(0)
                return ScriptedUI.confirm(self, prompt, default)
        ui = UI([
            ('Name of computer', ['Desktop', 'desktop', 'Media PC']),
            ('MAC address of', ['01-23-45-67-89-ab', 'not a mac', '02:00:00:00:00:01']),
            ('IP address of', ['192.0.2.10', '192.0.2.11']),
        ])
        before = sample_text()
        result = computers.run(ui, self.config)
        self.assertEqual(result, [('Desktop', '01:23:45:67:89:AB', '192.0.2.10'), ('Media PC', '02:00:00:00:00:01', '192.0.2.11')])
        self.assertEqual(self.get('COMPUTER_NAME'), ['Desktop', 'Media PC'])
        self.assertEqual(self.get('COMPUTER_MAC'), ['01:23:45:67:89:AB', '02:00:00:00:00:01'])
        self.assertEqual(self.get('COMPUTER_LOCAL_IP'), ['192.0.2.10', '192.0.2.11'])
        text = '\n'.join(str(x) for x in ui.out)
        self.assertIn('There is already a computer called desktop', text)
        self.assertIn('12 hexadecimal digits', text)
        self.assertIn('Saved 2 computers to config.php.', text)
        # Only the three list lines differ; sleep settings, comments and everything else are untouched
        after = self.config.read()
        changed = [a for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))
        self.assertEqual(len(changed), 3)
        self.assertEqual(self.get('COMPUTER_SLEEP_CMD'), 'suspend')
        self.assertNotIn(phpconfig.BLOCK_HEADER, after)
        self.assertIn('Physical Address', text)

    def existing(self):
        self.config.update({'COMPUTER_NAME': ['Desktop', 'Media PC'], 'COMPUTER_MAC': ['01:23:45:67:89:AB', '02:00:00:00:00:01'],
                            'COMPUTER_LOCAL_IP': ['192.0.2.10', '192.0.2.11']})

    def test_existing_list_is_shown_and_kept_by_default(self):
        self.existing()
        before = self.config.read()
        result, text = self.run_with([])
        self.assertEqual(len(result), 2)
        self.assertIn('Desktop', text)
        self.assertIn('01:23:45:67:89:AB', text)
        self.assertNotIn('Name of computer', text)
        self.assertEqual(self.config.read(), before)

    def test_remove_a_computer(self):
        self.existing()
        choices = [3, 1]

        class UI(ScriptedUI):
            def choose(self, query, options, default=None):
                if query == computers.LIST_QUERY:
                    i = choices.pop(0)
                    return i, options[i - 1]
                return ScriptedUI.choose(self, query, options, default)
        ui = UI([('Number of the computer to remove', ['9', '1'])])
        result = computers.run(ui, self.config)
        self.assertEqual([c[0] for c in result], ['Media PC'])
        self.assertEqual(self.get('COMPUTER_MAC'), ['02:00:00:00:00:01'])
        self.assertEqual(self.get('COMPUTER_LOCAL_IP'), ['192.0.2.11'])

    def test_last_computer_cannot_be_removed(self):
        self.config.update({'COMPUTER_NAME': ['Desktop'], 'COMPUTER_MAC': ['01:23:45:67:89:AB'], 'COMPUTER_LOCAL_IP': ['192.0.2.10']})
        choices = [3, 1]

        class UI(ScriptedUI):
            def choose(self, query, options, default=None):
                i = choices.pop(0)
                return i, options[i - 1]
        ui = UI([])
        self.assertEqual(len(computers.run(ui, self.config)), 1)
        self.assertIn('at least one computer', '\n'.join(str(x) for x in ui.out))

    def test_sleep_and_timing_settings_change_only_when_asked(self):
        self.existing()
        result, text = self.run_with([
            (computers.ADVANCED_QUERY, True),
            ('What should the sleep button do', computers.SLEEP_COMMANDS.index('hibernate') + 1),
            ('Port the SleepOnLan utility', ['0', '7761']),
            ('How many times to ping', '20'),
            ('Seconds to wait between pings', '3'),
        ])
        self.assertEqual(self.get('COMPUTER_SLEEP_CMD'), 'hibernate')
        self.assertEqual(self.get('COMPUTER_SLEEP_CMD_PORT'), 7761)
        self.assertEqual(self.get('MAX_PINGS'), 20)
        self.assertEqual(self.get('SLEEP_TIME'), 3)
        self.assertIn('Enter a number between 1 and 65535.', text)

    def test_setup_passes_the_password_to_php_on_stdin(self):
        with open(os.path.join(fakes.ROOT, 'setup.py'), newline='') as f:
            src = f.read()
        self.assertIn("password_hash(stream_get_contents(STDIN), PASSWORD_DEFAULT)", src)
        self.assertNotIn("' + p1 + '", src)
        self.assertIn('computers.run(ui, phpconfig.ConfigFile(str(rwsols_config_user)))', src)


if __name__ == '__main__':
    unittest.main()
