"""Cloudflare Access (Zero Trust) for the tunnel hostname: automatic provisioning or manual values."""
import re

from .errors import ApiError, SetupError
from .state import save_state

TEAM_DOMAIN_RE = re.compile(r'^[a-z0-9][a-z0-9-]*\.cloudflareaccess\.com$')
AUD_RE = re.compile(r'^[0-9a-f]{64}$')
EMAIL_RE = re.compile(r'^[^@\s,]+@[^@\s,]+\.[^@\s,]+$')
DOMAIN_RE = re.compile(r'^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$')
SESSION_QUERY = 'How long should a login last?'
SESSION_CHOICES = [('1 day', '24h'), ('1 week', '168h'), ('1 month', '720h')]   # label, Cloudflare duration
DEFAULT_SESSION = '720h'


def validate_team_domain(v):
    v = v.strip().lower()
    v = re.sub(r'^https?://', '', v).rstrip('/')
    if not TEAM_DOMAIN_RE.match(v):
        raise ValueError('Team domain should look like example.cloudflareaccess.com')
    return v


def validate_aud(v):
    v = v.strip().lower()
    if not AUD_RE.match(v):
        raise ValueError('The AUD tag is 64 hexadecimal characters')
    return v


def parse_identities(emails_text, domain_text):
    emails = [e.strip().lower() for e in emails_text.split(',') if e.strip()]
    for e in emails:
        if not EMAIL_RE.match(e):
            raise ValueError('%r is not a valid email address' % e)
    domains = [d.strip().lower().lstrip('@') for d in domain_text.split(',') if d.strip()]
    for d in domains:
        if not DOMAIN_RE.match(d):
            raise ValueError('%r is not a valid email domain' % d)
    if not emails and not domains:
        raise ValueError('Enter at least one email address or email domain')
    return emails, domains


def ask_identities(ui):
    ui.explain('Who may log in', [
        'Cloudflare Access emails a one-time code to the address a visitor types in; only people you list below get in.',
        'Emails: individual addresses that may log in (comma separated).',
        'Email domain: everyone with an address at that domain may log in (e.g. example.com). Use it for family or a team.',
        'Fill in either one, or both. At least one is required.'])
    while True:
        try:
            return parse_identities(ui.ask('Emails allowed to log in (comma separated, may be blank)', ''),
                                    ui.ask('Allow everyone with an email at this domain (e.g. example.com, may be blank)', ''))
        except ValueError as e:
            ui.warn(str(e))


def ask_session_duration(ui, current=None):
    """How long a browser stays logged in to Access before the emailed code is asked for again."""
    values = [v for _, v in SESSION_CHOICES]
    ui.explain('Login length', [
        'After someone logs in with the emailed code, that browser stays logged in for this long. Then Cloudflare asks '
        'for a new code.',
        'Longer means fewer codes to type. Shorter means a browser that someone else gets hold of stops working sooner. '
        'The RWSOLS passphrase is still asked for either way.'])
    default = values.index(current if current in values else DEFAULT_SESSION) + 1
    i, _ = ui.choose(SESSION_QUERY, [label for label, _ in SESSION_CHOICES], default)
    return values[i - 1]


def ask_manual(ui):
    ui.explain('Cloudflare Access values', [
        'In the Cloudflare Zero Trust dashboard (Access > Applications), add a Self-hosted application for your tunnel',
        'hostname with an Allow policy, then copy the "Application Audience (AUD) Tag" from its overview.',
        'The team domain is shown under Settings > Custom Pages / General (example.cloudflareaccess.com).'])
    team = ui.ask('Team domain', validator=validate_team_domain)
    aud = ui.ask('Application AUD tag', validator=validate_aud)
    return team, aud


class AccessProvisioner(object):
    def __init__(self, ctx, api, st, account_id, hostname):
        self.ctx, self.api, self.st = ctx, api, st
        self.acct, self.hostname = account_id, hostname
        self.a = st['access']
        self.session = DEFAULT_SESSION

    def team_domain(self):
        try:
            org = self.api.get('/accounts/%s/access/organizations' % self.acct) or {}
        except ApiError as e:
            raise SetupError('Could not read the Zero Trust organization (%s). Zero Trust must be set up on this '
                             'account first (free plan is fine): https://one.dash.cloudflare.com' % e)
        domain = org.get('auth_domain')
        if not domain:
            raise SetupError('This account has no Zero Trust team domain yet. Finish Zero Trust onboarding first.')
        return domain

    def _find_policy(self, name):
        for p in self.api.get_all('/accounts/%s/access/policies' % self.acct):
            if p.get('name') == name:
                return p
        return None

    @staticmethod
    def _include(emails, domains):
        return [{'email': {'email': e}} for e in emails] + [{'email_domain': {'domain': d}} for d in domains]

    def ensure_policy(self, name, decision, include, state_key):
        base = '/accounts/%s/access/policies' % self.acct
        existing = self._find_policy(name)
        if existing:
            ours = self.a.get(state_key) == existing.get('id')
            if ours and (existing.get('include') != include or existing.get('decision') != decision
                         or existing.get('session_duration') != self.session):
                self.api.mutate('PUT', '%s/%s' % (base, existing['id']),
                                {'name': name, 'decision': decision, 'include': include, 'session_duration': self.session}, {})
                self.ctx.ui.say('Updated Access policy %r.' % name)
            elif not ours and existing.get('include') != include:
                self.ctx.ui.warn('Access policy %r already exists and differs from your answers; it was not modified.' % name)
            self.a[state_key] = existing['id']
            if not ours:
                self.a[state_key + '_created'] = False
            return existing['id']
        res = self.api.mutate('POST', base, {'name': name, 'decision': decision, 'include': include, 'session_duration': self.session},
                              {'id': '<new-policy-id>'})
        self.a[state_key] = res['id']
        self.a[state_key + '_created'] = True
        self.ctx.ui.say('Created Access policy %r.' % name)
        return res['id']

    def ensure_app(self, policy_ids):
        base = '/accounts/%s/access/apps' % self.acct
        app = None
        for x in self.api.get_all(base):
            if x.get('type') == 'self_hosted' and (x.get('domain') or '').lower() == self.hostname:
                app = x
                break
        if app:
            ours = app.get('id') == self.a.get('app_id') and self.a.get('app_created')
            if app.get('id') != self.a.get('app_id'):
                self.a['app_created'] = False
            self.ctx.ui.say('Using existing Access application for %s.' % self.hostname)
            if app.get('session_duration') != self.session:
                if ours:
                    # PUT replaces the application, so send back what it already has with only the session changed.
                    body = {k: app[k] for k in ('type', 'name', 'domain', 'destinations') if k in app}
                    body['policies'] = [{'id': p['id'], 'precedence': p.get('precedence', i + 1)} if isinstance(p, dict) else p
                                        for i, p in enumerate(app.get('policies') or [])]
                    body['session_duration'] = self.session
                    self.api.mutate('PUT', '%s/%s' % (base, app['id']), body, {})
                    self.ctx.ui.say('Updated the login length of the Access application.')
                else:
                    self.ctx.ui.warn('The existing Access application was not created by this wizard, so its login length '
                                     '(%s) was not changed. Change it in the dashboard if intended.' % app.get('session_duration'))
            attached = set(p.get('id') if isinstance(p, dict) else p for p in app.get('policies') or [])
            for pid in policy_ids:
                if pid not in attached:
                    self.ctx.ui.warn('Policy %s is not attached to the existing Access application; attach it in the dashboard if intended.' % pid)
        else:
            body = {'type': 'self_hosted', 'name': 'RWSOLS (%s)' % self.hostname, 'domain': self.hostname,
                    'destinations': [{'type': 'public', 'uri': self.hostname}], 'session_duration': self.session,
                    'policies': [{'id': pid, 'precedence': i + 1} for i, pid in enumerate(policy_ids)]}
            app = self.api.mutate('POST', base, body, {'id': '<new-app-id>', 'aud': '0' * 64})
            self.a['app_created'] = True
            self.ctx.ui.say('Created Access application for %s.' % self.hostname)
        self.a['app_id'] = app['id']
        return app['aud']

    def ensure_service_token(self):
        if self.a.get('service_token_id'):
            self.ctx.ui.say('A service token was already created earlier (its secret cannot be shown again; rotate it in the dashboard if lost).')
            return None
        name = 'RWSOLS %s' % self.hostname
        res = self.api.mutate('POST', '/accounts/%s/access/service_tokens' % self.acct, {'name': name, 'duration': '8760h'},
                              {'id': '<new-token-id>', 'client_id': '<client-id>', 'client_secret': '<client-secret>'})
        self.a['service_token_id'] = res['id']
        self.a['service_token_created'] = True
        return res

    def run(self, emails, domains, want_service_token, session=DEFAULT_SESSION):
        """Returns (team_domain, aud, service_token_result_or_None)."""
        self.session = session
        team = self.team_domain()
        ids = [self.ensure_policy('RWSOLS %s allow' % self.hostname, 'allow', self._include(emails, domains), 'policy_id')]
        tok = None
        if want_service_token:
            tok = self.ensure_service_token()
            if self.a.get('service_token_id'):
                ids.append(self.ensure_policy('RWSOLS %s service auth' % self.hostname, 'non_identity',
                                              [{'service_token': {'token_id': self.a['service_token_id']}}], 'svc_policy_id'))
        aud = self.ensure_app(ids)
        self.a.update({'enabled': True, 'team_domain': team, 'session_duration': session})
        save_state(self.ctx, self.st)
        return team, aud, tok


def show_service_token_once(ui, tok):
    ui.explain('Service token (shown once)', [
        'It is not stored anywhere by this wizard.',
        '    CF-Access-Client-Id:     %s' % tok['client_id'],
        '    CF-Access-Client-Secret: %s' % tok['client_secret'],
        'The secret cannot be displayed again. Send both as request headers from non-browser clients. It expires after 1 year.'],
        style='warn')


def remove_app(ctx, st, api):
    """Delete the Access app/policies/service token only if the wizard created them."""
    a, notes = st.get('access', {}), []
    acct = (st.get('tunnel') or {}).get('account_id')
    if not (api and acct):
        if a.get('app_created'):
            notes.append('LEFT IN CLOUDFLARE (no API token given): Access application; delete it in the dashboard.')
        return notes
    if a.get('app_id'):
        if a.get('app_created'):
            api.mutate('DELETE', '/accounts/%s/access/apps/%s' % (acct, a['app_id']))
            notes.append('Deleted Access application')
        else:
            notes.append('LEFT ALONE: Access application (not created by this wizard)')
    for key in ('policy_id', 'svc_policy_id'):
        if a.get(key) and a.get(key + '_created'):
            api.mutate('DELETE', '/accounts/%s/access/policies/%s' % (acct, a[key]))
            notes.append('Deleted Access policy')
    if a.get('service_token_id') and a.get('service_token_created'):
        api.mutate('DELETE', '/accounts/%s/access/service_tokens/%s' % (acct, a['service_token_id']))
        notes.append('Deleted Access service token')
    return notes
