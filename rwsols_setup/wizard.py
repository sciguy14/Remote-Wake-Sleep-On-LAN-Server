"""Interactive remote-access flow used by setup.py: mode, allowlist, Cloudflare Tunnel, Access, removal."""
import json
import re
from urllib.parse import quote

from . import access, apache, dnscheck, tunnel
from .cloudflare_api import CloudflareAPI
from .errors import SetupError
from .hosts import validate_hostname
from .phpconfig import ConfigFile
from .state import MODES, default_mode, load_state, new_state, save_state
from .ui import Option

MODE_LABELS = {
    'none': 'None: LAN only. RWSOLS is only reachable from inside my home network.',
    'port_forward': 'Port forwarding: DDNS + router port forwards (80/443), optional Let\'s Encrypt certificate.',
    'tunnel': 'Cloudflare Tunnel: no inbound ports; optional Cloudflare Access login in front.',
}
MODE_ORDER = ['none', 'port_forward', 'tunnel']
MODE_HELP = [
    'LAN only: nothing is exposed to the internet. Pick this if you only use RWSOLS at home (or over your own VPN).',
    'Port forwarding: your router forwards ports 80/443 to this Pi and dynamic DNS keeps a name pointed at your home IP. '
    'Pick this if you already run it that way, or cannot use Cloudflare.',
    'Cloudflare Tunnel: this Pi opens an outbound connection to Cloudflare, so no router ports are needed and your home IP '
    'stays hidden. Needs a domain whose DNS is managed by Cloudflare (checked next); optional login (Access) in front.',
]
DEFAULTS = {'ALLOWED_HOSTS': [], 'ALLOW_LOCALHOST': False, 'LOG_HOST_REJECTIONS': False,
            'TUNNEL_HOSTNAME': None, 'TUNNEL_ORIGIN_BIND': '127.0.0.1', 'TUNNEL_ORIGIN_PORT': 8080,
            'ACCESS_TEAM_DOMAIN': None, 'ACCESS_AUD': None}
TOKEN_SCOPES = [
    'Account > Cloudflare Tunnel > Edit',
    'Zone > DNS > Edit            (restrict to the zone of your tunnel hostname)',
    'Zone > Zone > Read           (same zone)',
]
ACCESS_SCOPES = [
    'Account > Access: Apps and Policies > Edit',
    'Account > Access: Organizations, Identity Providers, and Groups > Read',
]
SERVICE_TOKEN_SCOPE = 'Account > Access: Service Tokens > Edit'
TOKEN_PAGE = 'https://dash.cloudflare.com/profile/api-tokens'
# Short keys for Cloudflare's "API token template URL" (a link that opens the Create Token form pre-filled):
# https://developers.cloudflare.com/fundamentals/api/how-to/account-owned-token-template/
# dns, zone, access and access_acct are in Cloudflare's reference table. argotunnel (Cloudflare Tunnel) is not
# listed there, but the dashboard accepted it when checked by hand in November 2026 (it ticked "Cloudflare
# Tunnel: Edit"). The dashboard silently drops a key it does not know, which is why the wizard always tells the
# user to compare the form with the printed list and then checks the pasted token itself.
TEMPLATE_KEYS = [('argotunnel', 'edit'), ('dns', 'edit'), ('zone', 'read')]
TEMPLATE_ACCESS_KEYS = [('access', 'edit'), ('access_acct', 'read')]
PASTE_QUERY = 'What would you like to do about this token?'
PASTE_OPTIONS = ['I fixed the token\'s permissions. Check it again.', 'Paste a different API token.',
                 'Use this token anyway.']


def token_name(hostname):
    """Suggested name for the API token, so it is easy to find (and delete) in the Cloudflare dashboard later."""
    return 'RWSOLS setup (%s)' % hostname if hostname else 'RWSOLS setup'


def token_template_url(name, access=False):
    """Link to Cloudflare's Create Token form with the name and permissions already filled in. It only fills in
    the form: the user still reviews it and creates the token there, and no token ever passes through the link."""
    keys = TEMPLATE_KEYS + (TEMPLATE_ACCESS_KEYS if access else [])
    perms = json.dumps([{'key': k, 'type': t} for k, t in keys], separators=(',', ':'))
    return '%s?permissionGroupKeys=%s&accountId=%s&zoneId=all&name=%s' % (
        TOKEN_PAGE, quote(perms, safe=''), quote('*', safe=''), quote(name, safe=''))
RECHECK_QUERY = 'What would you like to do about the dynamic DNS token?'
RECHECK_OPTIONS = ['I added the missing permission to the token. Check it again.', 'Paste a different API token instead.']
MAX_TOKEN_RECHECKS = 10
# Shown by the dynamic DNS step of setup.py when the user is about to create a Cloudflare token for ddclient.
DDNS_TOKEN_NOTE = [
    'Later in this setup you can choose Cloudflare Tunnel for remote access (no port forwarding). That step also needs '
    'a Cloudflare API token, and it can reuse this one if the token has more permissions than dynamic DNS needs.',
    'If you think you will use Cloudflare Tunnel, add these while you are creating the token:',
    '    Account > Cloudflare Tunnel > Edit',
    '  and, only if you also want the Cloudflare Access login in front of it:',
    '    Account > Access: Apps and Policies > Edit',
    '    Account > Access: Organizations, Identity Providers, and Groups > Read',
    'This is optional. If you skip it, the tunnel step checks the token, tells you what is missing, and lets you add '
    'the permission then (editing a token does not change its value) or paste a separate token.',
    'Trade-off: this token stays on the Pi for good, so extra permissions on it are stored here permanently. A separate '
    'token that you paste once at the tunnel step and delete afterwards is the tighter choice.',
]


def _optional_domain(v):
    v = v.strip()
    return _tunnel_hostname(v) if v else ''


def cloudflare_availability(ctx, current_mode=None):
    """Work out, before the mode question, whether Cloudflare Tunnel (and with it Access) can be offered.

    Returns (available, reason). `reason` is the sentence shown when the options are skipped. Names already
    known (the saved tunnel hostname, ddclient's names) are checked first; the user is only asked for a
    domain when none of them is on Cloudflare. A failed lookup never hides the option. The name that
    passed is remembered on ctx.cloudflare_base for the tunnel hostname suggestion."""
    ui = ctx.ui
    ctx.cloudflare_base = None
    st = load_state(ctx.state_path) or new_state()
    saved = st.get('tunnel', {}).get('hostname')
    ddns = ddclient_hosts(ctx.runner)
    ui.explain('Is Cloudflare an option for you?', [
        'Cloudflare Tunnel and Cloudflare Access only work with a domain whose DNS is managed by Cloudflare: the domain\'s '
        'nameservers point at Cloudflare. The free Cloudflare plan is enough, and the domain can be registered anywhere.',
        'How the domain\'s IP address is kept up to date does not matter. ddclient on this Pi, the dynamic DNS feature of your '
        'router, another service, or nothing at all: the tunnel does not use your home IP, so all of these work.',
        'The check below asks a public DNS server who hosts the domain. It needs no Cloudflare login.'])
    failed = []
    unknown = False
    seen = set()

    def check(name):
        c = dnscheck.check_domain(ctx.http, name)
        key = c.zone or c.name
        if key in seen:
            return c
        seen.add(key)
        if c.status == 'cloudflare':
            ui.success(dnscheck.describe(c) + ' Cloudflare Tunnel is available.')
        else:
            ui.warn(dnscheck.describe(c))
        return c

    for name in ([saved] if saved else []) + ddns:
        c = check(name)
        if c.status == 'cloudflare':
            if name != saved:
                ctx.cloudflare_base = name
            return True, ''
        if c.status == 'unknown':
            unknown = True
        else:
            failed.append(c)
    if not unknown:
        if ddns:
            ui.say('Your dynamic DNS names (%s) are not on Cloudflare. If you have another domain that is, enter it here.'
                   % ', '.join(ddns))
        while True:
            name = ui.ask('Domain to use with Cloudflare, e.g. example.com (leave blank if you have none)', None,
                          validator=_optional_domain)
            if not name:
                break
            c = check(name)
            if c.status == 'cloudflare':
                ctx.cloudflare_base = name
                return True, ''
            if c.status == 'unknown':
                unknown = True
                break
            failed.append(c)
            ui.say('Enter a different domain, or leave it blank to continue without Cloudflare.')
    if unknown:
        ui.hint('The Cloudflare options are shown anyway. The tunnel setup will stop with an error if the domain is not on Cloudflare.')
        return True, ''
    if current_mode == 'tunnel':
        ui.hint('This install already uses Cloudflare Tunnel, so the option stays available.')
        return True, ''
    if failed:
        reason = ' '.join(dnscheck.describe(c) for c in failed[:2])
    else:
        reason = 'You did not name a domain whose DNS is managed by Cloudflare.'
    return False, reason


def choose_mode(ctx):
    dflt = default_mode(ctx.repo_dir)
    ctx.ui.heading('Remote access')
    ctx.ui.explain('How will you reach RWSOLS from outside your home network?', MODE_HELP)
    available, reason = cloudflare_availability(ctx, dflt)
    order = list(MODE_ORDER)
    if not available:
        order.remove('tunnel')
        ctx.ui.warn('Skipping the Cloudflare Tunnel and Cloudflare Access options. ' + reason)
        ctx.ui.hint('To use them later: move the domain\'s DNS to Cloudflare (add the site in the Cloudflare dashboard and set the '
                    'nameservers it shows you at your registrar), then run: python3 setup.py --remote-access')
    if dflt == 'port_forward':
        ctx.ui.hint('An existing port-forwarding based install was detected, so that is the default.')
    i, _ = ctx.ui.choose('Choose a remote access mode.', [MODE_LABELS[m] for m in order], order.index(dflt) + 1)
    return order[i - 1]


def ddclient_hosts(runner):
    raw = runner.read_file('/etc/ddclient.conf')
    if not raw:
        return []
    lines = [l for l in raw.decode('utf8', 'replace').splitlines() if l.strip() and not l.lstrip().startswith('#')]
    hosts = []
    if lines and '=' not in lines[-1]:
        for h in lines[-1].split(','):
            try:
                hosts.append(validate_hostname(h))
            except ValueError:
                pass
    return hosts


def ddclient_cloudflare_token(runner):
    """The API token ddclient uses for Cloudflare dynamic DNS, or None.

    Only returned when /etc/ddclient.conf is unambiguous: every protocol= is cloudflare, every login= is
    the literal "token" (an API token, not the account-wide Global API Key), and there is one password.
    The value is only ever held in memory; it is never printed, logged or written anywhere."""
    raw = runner.read_file('/etc/ddclient.conf')
    if not raw:
        return None
    text = raw.decode('utf8', 'replace').replace('\\\n', ' ')
    text = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))

    def values(key):
        found = re.findall(r'(?:^|[\s,])' + key + r'=(\'[^\']*\'|"[^"]*"|[^\s,]+)', text)
        return [v[1:-1] if v[:1] in '\'"' else v for v in found]
    protocols, logins, passwords = values('protocol'), values('login'), set(values('password'))
    if not protocols or any(p != 'cloudflare' for p in protocols):
        return None
    if not logins or any(l != 'token' for l in logins):
        return None
    if len(passwords) != 1:
        return None
    token = passwords.pop()
    return token if len(token) >= 20 else None


def read_settings(cfg):
    return dict((k, cfg.get(k, v)) for k, v in DEFAULTS.items())


def _tunnel_hostname(v):
    h = validate_hostname(v)
    if '.' not in h or h.startswith('[') or re.fullmatch(r'[0-9.]+', h):
        raise ValueError('use a full domain name that is on Cloudflare')
    return h


class RemoteAccessWizard(object):
    def __init__(self, ctx, mode, suggested_hosts=(), lan_ip=None, copy_webroot=None):
        self.ctx, self.ui, self.mode = ctx, ctx.ui, mode
        self.suggested = list(suggested_hosts)
        self.lan_ip = lan_ip
        self.copy_webroot = copy_webroot
        self.cfg = ConfigFile(ctx.config_path, ctx.runner, ctx.dry_run)
        if not self.cfg.exists():
            raise SetupError('config.php not found at %s; run the full setup first.' % ctx.config_path)
        self.st = load_state(ctx.state_path) or new_state()
        self.cur = read_settings(self.cfg)
        self.new = dict(self.cur)
        self.method = None
        self.tunnel_name = 'rwsols'
        self.access_mode = 'off'
        self.session = access.DEFAULT_SESSION
        self.identities = ([], [])
        self.want_service_token = False
        self.manual_token = None
        self.service_token = None
        self.tunnel_token = None
        self.account_id = None
        self._api = None
        self.notes = []

    # ----- planning (questions only; no side effects) -----

    def plan(self):
        prev_tunnel = bool(self.st.get('tunnel', {}).get('hostname') or self.cur['TUNNEL_HOSTNAME'])
        if self.mode != 'tunnel' and prev_tunnel:
            self.ui.say('')
            self.ui.warn('A Cloudflare Tunnel setup from a previous run exists (%s).' % (self.st['tunnel'].get('hostname') or self.cur['TUNNEL_HOSTNAME']))
            if self.ui.confirm('Remove the tunnel setup now (service, vhost, config keys, and optionally Cloudflare resources)?', True):
                self.remove_tunnel()
                self.cur = read_settings(self.cfg)
                self.new = dict(self.cur)
        if self.mode == 'tunnel':
            self._plan_tunnel()
            self._plan_access()
        self._plan_allowlist()

    def _plan_tunnel(self):
        ui = self.ui
        ui.heading('Cloudflare Tunnel')
        ui.say('cloudflared makes an outbound-only connection to Cloudflare. Remove any router port forwards for RWSOLS.')
        prev = self.st.get('tunnel', {})
        ddns_names = ddclient_hosts(self.ctx.runner)
        default_host = self.new['TUNNEL_HOSTNAME'] or prev.get('hostname')
        base = getattr(self.ctx, 'cloudflare_base', None) or (ddns_names[0] if ddns_names else None)
        if not default_host and base:
            default_host = 'rwsols.' + base
            try:
                _tunnel_hostname(default_host)
            except ValueError:
                default_host = None
        lines = [
            'A dedicated subdomain (e.g. rwsols.example.com) is recommended over the bare domain (example.com).',
            'The tunnel takes over that name\'s DNS record (a proxied CNAME to Cloudflare). On its own name, RWSOLS leaves the '
            'bare domain free for dynamic DNS or a website, and you can add other tunnelled services later, each on its own name '
            '(e.g. nas.example.com).',
            'The bare domain works too, but then it cannot also be your dynamic DNS name.']
        if ddns_names:
            lines.append('Dynamic DNS keeps updating %s to your home IP exactly as before. The tunnel hostname is a separate DNS '
                         'record, and the two coexist.' % ', '.join(ddns_names))
        lines.append('The name must be in a domain whose DNS is managed by Cloudflare. The wizard checks this when you enter it.')
        ui.explain('Tunnel hostname', lines)

        def on_cloudflare(v):
            h = _tunnel_hostname(v)
            c = dnscheck.check_domain(self.ctx.http, h)
            if c.status in ('other', 'missing'):
                raise ValueError(dnscheck.describe(c) + ' Use a name in a domain that is on Cloudflare.')
            return h
        h = ui.ask('Public hostname for RWSOLS (e.g. rwsols.example.com)', default_host, validator=on_cloudflare)
        self.new['TUNNEL_HOSTNAME'] = h
        if tunnel.ddns_overlap(h, '\n'.join([', '.join(ddns_names)])):
            ui.warn('%s is also in your ddclient host list. The tunnel owns that DNS record (a proxied CNAME); '
                    'ddclient would keep overwriting it with your IP. Remove it from /etc/ddclient.conf.' % h)
        ui.explain('Origin port', [
            'cloudflared forwards visitors to a small Apache listener that only this Pi can reach (%s, loopback only). '
            'The LAN site on ports 80/443 is not touched.' % self.new['TUNNEL_ORIGIN_BIND'],
            'The default is fine unless something else on the Pi already uses that port.'])
        self.new['TUNNEL_ORIGIN_PORT'] = ui.ask('Local port for the loopback Apache listener cloudflared connects to',
                                                str(self.new['TUNNEL_ORIGIN_PORT']), validator=self._origin_port)
        ui.explain('Automatic or manual setup', [
            'Automatic: you paste a Cloudflare API token (created in the dashboard, used only for this run) and the wizard creates '
            'the tunnel, the DNS record and the service for you. Needs your domain on Cloudflare.',
            'Manual: you create the tunnel and its public hostname in the Cloudflare dashboard yourself and paste the tunnel token. '
            'No API token needed; more clicking, fewer permissions given away.'])
        i, _ = ui.choose('How should the tunnel be set up?', [
            'Automatic: I will provide a Cloudflare API token and the wizard creates the tunnel, DNS record, and service.',
            'Manual: I created the tunnel in the Cloudflare dashboard and will paste its tunnel token.'],
            1 if prev.get('method', 'automatic') == 'automatic' else 2)
        self.method = 'automatic' if i == 1 else 'manual'
        if self.method == 'automatic':
            self.tunnel_name = ui.ask('Tunnel name', prev.get('name') or 'rwsols')
        else:
            ui.explain('Create the tunnel in the dashboard', [
                'In the Cloudflare Zero Trust dashboard (Networks > Tunnels), create a Cloudflared tunnel and add a public hostname:',
                '    Hostname: %s' % h,
                '    Service:  HTTP  %s:%d' % (self.new['TUNNEL_ORIGIN_BIND'], self.new['TUNNEL_ORIGIN_PORT']),
                'Then copy the tunnel token (the long string after "cloudflared ... --token").'])

    def _origin_port(self, v):
        if not (v.isdigit() and 1024 <= int(v) <= 65535):
            raise ValueError('Enter a port between 1024 and 65535.')
        bind = self.new['TUNNEL_ORIGIN_BIND']
        if not tunnel.check_origin_port(self.ctx, self.st, bind, int(v)):
            raise ValueError('Port %s on %s is already in use. Choose another.' % (v, bind))
        return int(v)

    def _plan_access(self):
        ui = self.ui
        ui.heading('Cloudflare Access (optional)')
        ui.explain('What Access adds', [
            'Access puts a login (email one-time code, SSO, ...) in front of RWSOLS at Cloudflare\'s edge, so strangers never reach the Pi. '
            'RWSOLS also verifies the Access token and refuses tunnel requests without it.',
            'Recommended if the address will be reachable from anywhere. Skip it if the RWSOLS password alone is enough for you.',
            'The Cloudflare Zero Trust free plan is enough for a home setup, but Zero Trust must be set up on your account first.'])
        enabled_before = bool(self.cur['ACCESS_AUD'])
        if not ui.confirm('Enable Cloudflare Access for %s?' % self.new['TUNNEL_HOSTNAME'], enabled_before):
            self.access_mode = 'off'
            self.new.update(ACCESS_TEAM_DOMAIN=None, ACCESS_AUD=None)
            return
        if self.method == 'automatic':
            self.access_mode = 'automatic'
            self.identities = access.ask_identities(ui)
            self.session = access.ask_session_duration(ui, self.st.get('access', {}).get('session_duration'))
            ui.explain('Service token (most people should answer No)', [
                'Cloudflare Access asks a person to log in through a web page. A program cannot do that. A service token is a '
                'pair of secret values that a program sends instead, so it gets through without the login page.',
                'Example: a phone shortcut, a home automation system or a script that wakes your computer by calling RWSOLS '
                'directly, with nobody opening the page in a browser.',
                'If you only ever use RWSOLS by opening it in a browser, you do not need one. Answer No. You can add one '
                'later by running this again.',
                'If you answer Yes: the API token needs one more permission (%s), and the secret is shown once at the end '
                'and never stored.' % SERVICE_TOKEN_SCOPE])
            self.want_service_token = ui.confirm('Also create a service token for non-browser clients (scripts, apps)?', False)
        else:
            self.access_mode = 'manual'
            team, aud = access.ask_manual(ui)
            self.new.update(ACCESS_TEAM_DOMAIN=team, ACCESS_AUD=aud)

    def _lan_name_candidate(self):
        try:
            return validate_hostname(self.ctx.hostname_fn().split('.')[0] + '.local')
        except ValueError:
            return None

    def _reverse_dns_candidate(self):
        """The name the LAN's DNS gives this Pi's IP, only if it resolves back to that same IP. Never raises."""
        ip = self.lan_ip
        if not ip:
            return None
        try:
            name = self.ctx.resolver.reverse(ip)
            if not name:
                return None
            name = validate_hostname(name)
            if '.' not in name or name.startswith('[') or re.fullmatch(r'[0-9.]+', name):
                return None
            if ip not in (self.ctx.resolver.forward(name) or []):
                return None
            return name
        except Exception:
            return None

    def _search_domain_candidates(self):
        """<hostname>.<DNS search domain> names that resolve to this Pi's LAN IP. Never raises."""
        found = []
        try:
            raw = self.ctx.runner.read_file('/etc/resolv.conf')
            if not raw or not self.lan_ip:
                return found
            short = self.ctx.hostname_fn().split('.')[0]
            for line in raw.decode('utf8', 'replace').splitlines():
                parts = line.split()
                if len(parts) < 2 or parts[0] not in ('search', 'domain'):
                    continue
                for domain in parts[1:]:
                    try:
                        name = validate_hostname(short + '.' + domain)
                    except ValueError:
                        continue
                    if name not in found and self.lan_ip in (self.ctx.resolver.forward(name) or []):
                        found.append(name)
        except Exception:
            pass
        return found

    def allowlist_options(self):
        """Candidate names for the allowlist multi-select, in display order, with reasons and default ticks."""
        tunnel_mode = self.mode == 'tunnel'
        pf_mode = self.mode == 'port_forward'
        saved = []
        for h in self.cur['ALLOWED_HOSTS']:
            try:
                saved.append(validate_hostname(h))
            except ValueError:
                pass
        opts = []

        def add(name, why, checked=False, locked=False):
            try:
                name = validate_hostname(name)
            except (ValueError, TypeError):
                return
            if name not in [o.value for o in opts]:
                opts.append(Option(name, why, checked, locked))

        if tunnel_mode:
            add(self.new['TUNNEL_HOSTNAME'], 'your Cloudflare Tunnel hostname (required in tunnel mode)', True, True)
        local = self._lan_name_candidate()
        if local:
            add(local, "this Pi's mDNS name; works from most devices on your home network", True)
        rdns = self._reverse_dns_candidate()
        if rdns:
            add(rdns, "your router's local DNS name for this Pi (reverse lookup of %s)" % self.lan_ip, True)
        for name in self._search_domain_candidates():
            add(name, "this Pi's name in your network's DNS search domain (resolves to %s)" % self.lan_ip, True)
        if self.lan_ip:
            add(self.lan_ip, "this Pi's LAN IP; only tick it if you browse by IP, and reserve the address in your router so it does not change",
                self.lan_ip in saved)
        ddns = ddclient_hosts(self.ctx.runner)
        if pf_mode:
            ddns = list(dict.fromkeys(list(self.suggested) + ddns))
        for h in saved:
            if h not in ddns:
                add(h, 'already on your list', True)
        for h in ddns:
            if pf_mode:
                add(h, "your dynamic DNS name; needed for access through your router's port forwards", True)
            else:
                add(h, 'dynamic DNS name from your port forwarding setup; only needed while router port forwards still point at this Pi')
        return opts

    def _plan_allowlist(self):
        ui = self.ui
        ui.heading('Hostname allowlist (optional)')
        ui.explain('Why an allowlist', [
            'DNS rebinding: a malicious website can make its own name suddenly point at your Pi, so your browser (inside your home '
            'network) talks to RWSOLS on its behalf.',
            'An allowlist makes RWSOLS (Apache) answer only to the host names you list; every other name gets an empty HTTP 421.'])
        had = bool(self.cur['ALLOWED_HOSTS'])
        i, _ = ui.choose('Enable the hostname allowlist?', ['Yes, enable it (recommended).', 'No, accept any Host name (current default behavior).'],
                         1 if had else 2)
        if i == 2:
            self.new.update(ALLOWED_HOSTS=[], ALLOW_LOCALHOST=False)
            return
        hosts = ui.choose_many('Allowed host names: tick the names RWSOLS should answer to', self.allowlist_options(),
                               validator=validate_hostname, add_prompt='Host name or IP to add')
        if self.mode == 'tunnel' and self.new['TUNNEL_HOSTNAME'] not in hosts:
            hosts.insert(0, self.new['TUNNEL_HOSTNAME'])
        self.new['ALLOWED_HOSTS'] = list(dict.fromkeys(hosts))
        ui.explain('Lockout warning', [
            'Once enabled, browsing RWSOLS by any name or IP that is NOT on the list stops working.',
            'To recover: re-run "python3 setup.py --remote-access" and choose not to enable the allowlist.'], style='warn')
        ui.hint('Localhost (127.0.0.1) only matters if you open RWSOLS in a browser on the Pi itself or use a local health check.')
        self.new['ALLOW_LOCALHOST'] = ui.confirm('Also allow localhost / 127.0.0.1 / [::1]?', bool(self.cur['ALLOW_LOCALHOST']))
        ui.hint('Rejected requests are logged to syslog (rate limited), which helps you see what you forgot to list.')
        self.new['LOG_HOST_REJECTIONS'] = ui.confirm('Log rejected requests to syslog (rate limited)?', bool(self.cur['LOG_HOST_REJECTIONS']))

    # ----- cloud phase -----

    def _get_api(self):
        if self._api:
            return self._api
        ui = self.ui
        token = self.ctx.env.pop('CLOUDFLARE_API_TOKEN', None)
        if not token:
            api = self._try_ddclient_token()
            if api:
                self._api = api
                return api
            self._explain_new_token()
            for _ in range(MAX_TOKEN_RECHECKS):
                token = ui.ask_secret('Cloudflare API token')
                if not token:
                    raise SetupError('No API token provided.')
                api = CloudflareAPI(token, self.ctx.http, ui, self.ctx.dry_run)
                if self._vet_pasted_token(api):
                    self._api = api
                    return api
            raise SetupError('No usable API token provided.')
        self._api = CloudflareAPI(token, self.ctx.http, ui, self.ctx.dry_run)
        return self._api

    def _needs_access_scopes(self):
        return self.access_mode == 'automatic' or bool(self.st.get('access', {}).get('app_id'))

    def _scope_lines(self):
        """The permissions this run needs, and no others: Access ones only with Access, the service token one
        only when a service token was asked for."""
        lines = ['    ' + x for x in TOKEN_SCOPES]
        if self._needs_access_scopes():
            lines.append('  and for Cloudflare Access:')
            lines += ['    ' + x for x in ACCESS_SCOPES]
            if self.want_service_token:
                lines.append('    ' + SERVICE_TOKEN_SCOPE)
        return lines

    def _explain_new_token(self):
        ui = self.ui
        hostname = self.new.get('TUNNEL_HOSTNAME') or self.st.get('tunnel', {}).get('hostname')
        name = token_name(hostname)
        ui.explain('Cloudflare API token', [
            'The wizard needs it to create the tunnel and the DNS record (and the Access login) in your Cloudflare account.',
            'It is used only during this run, kept in memory and never written to disk or config.',
            'The link below opens Cloudflare\'s Create Token page with the name and permissions already filled in. '
            'Check them against this list before you create the token, and add any that are missing:']
            + self._scope_lines()
            + ['Suggested name: %s' % name]
            + (['The link cannot pre-select %s. Add that one on the page yourself (Add more > Account).' % SERVICE_TOKEN_SCOPE]
               if self._needs_access_scopes() and self.want_service_token else [])
            + ['The link selects all your zones and accounts. You can narrow it on that page to the zone of %s.' % (hostname or 'your tunnel hostname'),
               'Then: Continue to summary > Create Token > copy the token and paste it here.',
               '(No link? Create a Custom Token at %s. Or set the CLOUDFLARE_API_TOKEN environment variable and re-run.)' % TOKEN_PAGE])
        ui.say('Open this link in a browser:')
        ui.link(token_template_url(name, self._needs_access_scopes()))

    def _vet_pasted_token(self, api):
        """Checks a pasted token before anything is changed. True = use it, False = ask for another one."""
        ui = self.ui
        for _ in range(MAX_TOKEN_RECHECKS):
            gaps = self._token_gaps(api)
            if not gaps:
                ui.success('The token has the permissions this needs.')
                return True
            ui.warn('This token is missing something:')
            for g in gaps:
                ui.warn('  - ' + g)
            ui.say('To fix it: open %s, choose Edit on the token, add what is listed above, then "Continue to summary" '
                   'and "Update token". The token value does not change.' % TOKEN_PAGE)
            choice = ui.choose(PASTE_QUERY, PASTE_OPTIONS)[0]
            if choice == 2:
                return False
            if choice == 3:
                return True
            ui.say('Checking the token again...')
        return True     # the check itself may be wrong; the real calls report a clear error if the token is not enough

    def _token_gaps(self, api):
        """What `api`'s token cannot do for this run, as a list of sentences (empty = it looks sufficient).
        Read-only calls: they prove the token can see the zone, the account's tunnels and Access, not that
        it may change them. A token with read-only permissions still fails later, with a clear error."""
        hostname = self.new.get('TUNNEL_HOSTNAME') or self.st.get('tunnel', {}).get('hostname')
        if not hostname:
            return ['no tunnel hostname is known yet']
        account = None
        try:
            labels = hostname.split('.')
            for i in range(len(labels) - 1):
                res = api.get('/zones', {'name': '.'.join(labels[i:])})
                if res:
                    account = (res[0].get('account') or {}).get('id')
                    break
        except SetupError:
            return ['Cloudflare rejected it when looking up the zone (the token may have been revoked).']
        if not account:
            return ['it cannot see the Cloudflare zone of %s (needs Zone > Zone > Read for that zone).' % hostname]
        gaps = []
        try:
            api.get('/accounts/%s/cfd_tunnel' % account, {'name': self.tunnel_name, 'is_deleted': 'false'})
        except SetupError:
            gaps.append('it lacks the permission Account > Cloudflare Tunnel > Edit.')
        if self._needs_access_scopes():
            try:
                api.get('/accounts/%s/access/apps' % account)
            except SetupError:
                gaps.append('it lacks the permission Account > Access: Apps and Policies > Edit.')
        return gaps

    def _try_ddclient_token(self):
        """Offer the Cloudflare API token that ddclient already uses for dynamic DNS. Returns a ready
        CloudflareAPI, or None when there is no such token, the user declines, or it lacks permissions."""
        ui = self.ui
        token = ddclient_cloudflare_token(self.ctx.runner)
        if not token:
            return None
        ui.explain('Reuse the dynamic DNS token?', [
            'ddclient on this Pi already has a Cloudflare API token for dynamic DNS (in /etc/ddclient.conf). The wizard can '
            'use the same one, so you do not have to create and paste another.',
            'It only works if that token also has the permissions listed below. A token made just for dynamic DNS usually has '
            'the two Zone permissions but not the Tunnel one. The wizard checks, and tells you what is missing.',
            'Needed:'] + self._scope_lines()
            + ['Trade-off: the ddclient token stays on this Pi permanently, so adding permissions to it means a more powerful '
               'token is stored here for good. A separate token that you paste once and then delete in the Cloudflare dashboard '
               'is the tighter choice. Both work.'])
        if not ui.confirm('Check whether the dynamic DNS token can be used?', True):
            return None
        api = CloudflareAPI(token, self.ctx.http, ui, self.ctx.dry_run)
        for _ in range(MAX_TOKEN_RECHECKS):
            gaps = self._token_gaps(api)
            if not gaps:
                ui.success('The dynamic DNS token has the permissions this needs. Using it; nothing to paste.')
                return api
            ui.warn('The dynamic DNS token cannot be used as it is:')
            for g in gaps:
                ui.warn('  - ' + g)
            ui.explain('To reuse this token', [
                'Leave this window open and add the missing permission in a browser:',
                '  1. Open https://dash.cloudflare.com/profile/api-tokens',
                '  2. On the token ddclient uses, open the "..." menu and choose Edit.',
                '  3. Add the permission(s) listed above, then "Continue to summary" and "Update token".',
                'The token value does not change, so nothing needs editing on this Pi. Then come back and check again.',
                'Or skip this and paste a different token.'])
            if ui.choose(RECHECK_QUERY, RECHECK_OPTIONS)[0] != 1:
                return None
            ui.say('Checking the token again...')
        ui.warn('Still not usable after %d checks. Continuing with a pasted token.' % MAX_TOKEN_RECHECKS)
        return None

    def prepare_cloud(self):
        """Talks to Cloudflare (automatic) or collects the pasted token (manual). May write state."""
        if self.mode != 'tunnel':
            return
        ui, st = self.ui, self.st
        t = st['tunnel']
        h, bind, port = self.new['TUNNEL_HOSTNAME'], self.new['TUNNEL_ORIGIN_BIND'], self.new['TUNNEL_ORIGIN_PORT']
        t.update(hostname=h, origin_bind=bind, origin_port=port, method=self.method)
        if self.method == 'manual':
            while True:
                tok = ui.ask_secret('Tunnel token')
                try:
                    info = tunnel.parse_tunnel_token(tok)
                    break
                except SetupError as e:
                    ui.warn(str(e))
            self.tunnel_token = tok
            t.update(id=info['tunnel_id'], account_id=info['account_id'], created_tunnel=False, dns_created=False)
            return
        api = self._get_api()
        prov = tunnel.CloudProvisioner(self.ctx, api, st, h, self.tunnel_name, bind, port)
        self.tunnel_token = prov.run()
        self.account_id = prov.account_id
        if self.access_mode == 'automatic':
            ap = access.AccessProvisioner(self.ctx, api, st, prov.account_id, h)
            team, aud, tok = ap.run(self.identities[0], self.identities[1], self.want_service_token, self.session)
            self.new.update(ACCESS_TEAM_DOMAIN=team, ACCESS_AUD=aud)
            self.service_token = tok
        elif st.get('access', {}).get('app_created') and st['access'].get('app_id'):
            if ui.confirm('Access was turned off. Delete the Access application the wizard created earlier?', True):
                for n in access.remove_app(self.ctx, st, api):
                    ui.say(n)
                st['access'] = {}
        save_state(self.ctx, st)

    # ----- config + local phase -----

    def write_config(self):
        updates = {}
        for k, v in self.new.items():
            if v != self.cur[k]:
                updates[k] = v
        if self.mode == 'tunnel':
            updates['TUNNEL_HOSTNAME'] = self.new['TUNNEL_HOSTNAME']
        if updates:
            self.cfg.update(updates)
            self.ui.say('config.php updated: ' + ', '.join('$' + k for k in updates))

    def apply_local(self):
        ctx, ui, st = self.ctx, self.ui, self.st
        n = self.new
        tunnel_on = self.mode == 'tunnel'
        access_on = bool(n['ACCESS_AUD'] and n['ACCESS_TEAM_DOMAIN'])
        if tunnel_on:
            apache.apply_tunnel_site(ctx, n['TUNNEL_ORIGIN_BIND'], n['TUNNEL_ORIGIN_PORT'], access_on)
            st['tunnel']['vhost_installed'] = True
        conf = apache.render_allowed_hosts_conf(
            n['ALLOWED_HOSTS'], n['ALLOW_LOCALHOST'], n['TUNNEL_HOSTNAME'] if tunnel_on else None, access_on and tunnel_on)
        if apache.apply_allowed_hosts(ctx, conf):
            ui.say('Apache host allowlist ' + ('applied.' if conf else 'removed.'))
        failures = []
        if tunnel_on:
            tunnel.install_cloudflared(ctx, st)
            if not self.tunnel_token:
                if ctx.dry_run:
                    ui.say('[dry-run] would install the tunnel token service (token not fetched in dry-run).')
                else:
                    raise SetupError('No tunnel token available.')
            else:
                tunnel.install_service(ctx, self.tunnel_token)
            st['tunnel']['service_installed'] = True
            t = st['tunnel']
            results = tunnel.verify(ctx, n['TUNNEL_HOSTNAME'], n['TUNNEL_ORIGIN_BIND'], n['TUNNEL_ORIGIN_PORT'],
                                    access_on, self._api, t.get('account_id'), t.get('id'))
            if results:
                ui.checklist('Verifying the tunnel', results)
            failures = [label for label, ok, detail in results if not ok]
        st['mode'] = self.mode
        st['access'].update(enabled=access_on)
        st['allowlist'] = {'enabled': bool(n['ALLOWED_HOSTS'])}
        save_state(ctx, st)
        self._final_notes(failures)
        self.tunnel_token = None
        self._api = None

    def _final_notes(self, failures):
        ui = self.ui
        if self.service_token:
            access.show_service_token_once(ui, self.service_token)
        lines, warnings = [], []
        if self.mode == 'tunnel':
            lines.append('Cloudflare Tunnel is set up: https://%s' % self.new['TUNNEL_HOSTNAME'])
            warnings.append('Remove any router port forwards (80/443) that point at RWSOLS; they are no longer needed.')
            if self.ctx.runner.path_exists('/etc/apache2/sites-enabled/000-default-le-ssl.conf'):
                warnings.append('A Let\'s Encrypt certificate issued via certbot --apache renews over HTTP-01 on port 80, which will stop '
                                'working once port 80 is closed. Switch to a DNS-01 challenge (e.g. a certbot DNS plugin) or use a self-signed LAN certificate.')
            if failures:
                warnings.append('Some checks failed: ' + '; '.join(failures))
        if self.new['ALLOWED_HOSTS']:
            lines.append('Allowed hosts: ' + ', '.join(self.new['ALLOWED_HOSTS']))
            lines.append('TLS note: Apache still completes the TLS handshake for unlisted SNI names; the allowlist rejects them at the HTTP layer.')
        if lines or warnings:
            ui.summary('Remote access summary', lines, warnings)

    # ----- removal -----

    def remove_tunnel(self):
        ctx, ui, st = self.ctx, self.ui, self.st
        t = st.get('tunnel', {})
        if not ui.confirm('Tear down the Cloudflare Tunnel setup created by this wizard?', False):
            return False
        api = None
        needs_cloud = t.get('dns_created') or t.get('created_tunnel') or st.get('access', {}).get('app_created')
        if needs_cloud and t.get('method') == 'automatic':
            if ctx.env.get('CLOUDFLARE_API_TOKEN') or ui.confirm('Provide an API token to delete the Cloudflare tunnel, DNS record and Access objects?', True):
                api = self._get_api()
        lines = access.remove_app(ctx, st, api) if api else []
        lines += tunnel.remove(ctx, st, api)
        host = t.get('hostname') or self.cur['TUNNEL_HOSTNAME']
        hosts = [x for x in self.cur['ALLOWED_HOSTS'] if x != host]
        self.cfg.update(dict(TUNNEL_HOSTNAME=None, ACCESS_TEAM_DOMAIN=None, ACCESS_AUD=None, ALLOWED_HOSTS=hosts))
        self.new = dict(self.cur, TUNNEL_HOSTNAME=None, ACCESS_TEAM_DOMAIN=None, ACCESS_AUD=None, ALLOWED_HOSTS=hosts)
        if self.copy_webroot and not ctx.dry_run:
            self.copy_webroot()
            lines.append('Cleared tunnel/Access keys in config.php and copied it to the webroot')
        else:
            lines.append('Cleared tunnel/Access keys in config.php (re-run setup to copy it to the webroot)')
        apache.apply_allowed_hosts(ctx, apache.render_allowed_hosts_conf(hosts, self.cur['ALLOW_LOCALHOST']))
        ui.explain('Tunnel removal', ['- ' + ln for ln in lines])
        if ctx.runner.run(['which', 'cloudflared'], check=False, read_only=True).returncode == 0 and \
                ui.confirm('Also uninstall the cloudflared package?', False):
            ctx.runner.run(['apt-get', 'remove', '-y', 'cloudflared'], sudo=True)
        st['tunnel'], st['access'], st['mode'] = {}, {}, 'none'
        save_state(ctx, st)
        self._api = None
        return True


def run_remote_access(ctx, suggested_hosts=(), lan_ip=None, copy_webroot=None, mode=None):
    """Standalone `--remote-access`: mode question, plan, cloud, config, copy webroot, apply."""
    mode = mode or choose_mode(ctx)
    if not suggested_hosts and mode == 'port_forward':
        suggested_hosts = ddclient_hosts(ctx.runner)
    w = RemoteAccessWizard(ctx, mode, suggested_hosts, lan_ip, copy_webroot)
    w.plan()
    w.prepare_cloud()
    w.write_config()
    if copy_webroot and not ctx.dry_run:
        copy_webroot()
    w.apply_local()
    return w


def run_remove_tunnel(ctx, copy_webroot=None):
    w = RemoteAccessWizard(ctx, 'none', copy_webroot=copy_webroot)
    return w.remove_tunnel()
