import io
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from fakes import API_TOKEN, Env

from rwsols_setup import ui as uimod
from rwsols_setup import wizard

HOST = 'rwsols.example.com'
WEAK_TOKEN = 'WEAKTOKEN-0123456789abcdef'
ACCESS = [('Enable Cloudflare Access', True), ('Emails allowed', 'me@example.com')]


def keys(url):
    return [(p['key'], p['type']) for p in json.loads(parse_qs(urlsplit(url).query)['permissionGroupKeys'][0])]


class TemplateUrlTests(unittest.TestCase):
    def test_link_prefills_name_and_tunnel_permissions(self):
        url = wizard.token_template_url(wizard.token_name(HOST))
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ('https', 'dash.cloudflare.com', '/profile/api-tokens'))
        q = parse_qs(parts.query)
        self.assertEqual(q['name'], ['RWSOLS setup (%s)' % HOST])
        self.assertEqual((q['accountId'], q['zoneId']), (['*'], ['all']))
        self.assertEqual(keys(url), [('argotunnel', 'edit'), ('dns', 'edit'), ('zone', 'read')])
        self.assertNotIn(' ', url)
        self.assertNotIn('"', url)

    def test_access_permissions_only_when_access_is_wanted(self):
        self.assertEqual(keys(wizard.token_template_url('x', access=True))[3:], [('access', 'edit'), ('access_acct', 'read')])

    def test_name_without_a_hostname(self):
        self.assertEqual(wizard.token_name(None), 'RWSOLS setup')


class PromptTests(unittest.TestCase):
    def run_wizard(self, rules=(), secrets=(API_TOKEN,)):
        env = Env([('Public hostname', HOST)] + list(rules), token=False, secrets=list(secrets))
        self.addCleanup(env.cleanup)
        env.cf.limited_tokens[WEAK_TOKEN] = ('cfd_tunnel',)
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        w.prepare_cloud()
        return env

    def link(self, env):
        return [l for l in env.ui.out if l.startswith(wizard.TOKEN_PAGE + '?')]

    def test_tunnel_only_shows_link_name_and_no_access_permissions(self):
        env = self.run_wizard()
        text = env.ui.text()
        self.assertEqual(len(self.link(env)), 1)
        self.assertEqual(len(keys(self.link(env)[0])), 3)
        self.assertIn('Suggested name: RWSOLS setup (%s)' % HOST, text)
        self.assertNotIn('Access: Apps and Policies', text)
        self.assertNotIn('Service Tokens', text.split('Cloudflare API token')[1])
        self.assertIn('The token has the permissions this needs.', text)

    def test_access_without_service_token_never_mentions_its_permission_at_the_token_step(self):
        env = self.run_wizard(ACCESS)
        token_step = env.ui.text().split('The wizard needs it to create the tunnel')[1]
        self.assertIn('Access: Apps and Policies > Edit', token_step)
        self.assertNotIn('Service Tokens', token_step)
        self.assertEqual(len(keys(self.link(env)[0])), 5)

    def test_service_token_permission_is_listed_when_one_was_asked_for(self):
        env = self.run_wizard(ACCESS + [('service token', True)])
        token_step = env.ui.text().split('The wizard needs it to create the tunnel')[1]
        self.assertIn('    ' + wizard.SERVICE_TOKEN_SCOPE, token_step)
        self.assertIn('The link cannot pre-select', token_step)

    def test_service_token_question_explains_itself_and_defaults_to_no(self):
        env = self.run_wizard(ACCESS)
        text = env.ui.text()
        self.assertIn('Service token (most people should answer No)', text)
        self.assertIn('Example:', text)
        self.assertEqual(env.cf.service_tokens, [])

    def test_weak_pasted_token_is_reported_and_another_can_be_pasted(self):
        env = self.run_wizard([(wizard.PASTE_QUERY, 2)], secrets=[WEAK_TOKEN, API_TOKEN])
        text = env.ui.text()
        self.assertIn('This token is missing something:', text)
        self.assertIn('it lacks the permission Account > Cloudflare Tunnel > Edit.', text)
        self.assertEqual(env.ui.out.count('Cloudflare API token'), 3)      # the box title, then two prompts
        self.assertTrue(env.cf.tunnels)
        mutating = [t for t, c in zip(env.cf.tokens_used, env.cf.calls) if c[0] != 'GET']
        self.assertEqual(set(mutating), {API_TOKEN})
        self.assertNotIn(WEAK_TOKEN, text)

    def test_weak_pasted_token_can_be_fixed_and_checked_again(self):
        env = Env([('Public hostname', HOST)], token=False, secrets=[WEAK_TOKEN])
        self.addCleanup(env.cleanup)
        env.cf.limited_tokens[WEAK_TOKEN] = ('cfd_tunnel',)
        choose = env.ui.choose

        def choosing(query, options, default=None):
            if query == wizard.PASTE_QUERY:
                env.cf.limited_tokens[WEAK_TOKEN] = ()
            return choose(query, options, default)
        env.ui.choose = choosing
        w = wizard.RemoteAccessWizard(env.ctx, 'tunnel', [], None)
        w.plan()
        w.prepare_cloud()
        self.assertIn('Checking the token again...', env.ui.text())
        self.assertTrue(env.cf.tunnels)
        self.assertEqual(set(env.cf.tokens_used), {WEAK_TOKEN})


class LinkOutputTests(unittest.TestCase):
    URL = wizard.token_template_url(wizard.token_name(HOST), access=True)

    def test_plain_ui_prints_the_link_on_one_line(self):
        out = io.StringIO()
        uimod.PlainUI(out=out, color=False).link(self.URL)
        self.assertIn(self.URL + '\n', out.getvalue())

    @unittest.skipUnless(uimod.rich_available(), 'rich is not installed')
    def test_rich_ui_does_not_break_the_link_in_a_narrow_terminal(self):
        from rich.console import Console
        out = io.StringIO()
        uimod.RichUI(Console(file=out, force_terminal=False, width=40, highlight=False)).link(self.URL)
        self.assertIn(self.URL, out.getvalue())


if __name__ == '__main__':
    unittest.main()
