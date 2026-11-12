"""Interactive remote-access flow used by setup.py: mode, allowlist, Cloudflare Tunnel, Access, removal."""
import re

from . import access, apache, tunnel
from .cloudflare_api import CloudflareAPI
from .errors import SetupError
from .hosts import validate_hostname
from .phpconfig import ConfigFile
from .state import MODES, default_mode, load_state, new_state, save_state

MODE_LABELS = {
    'none': 'None: LAN only. RWSOLS is only reachable from inside my home network.',
    'port_forward': 'Port forwarding: DDNS + router port forwards (80/443), optional Let\'s Encrypt certificate.',
    'tunnel': 'Cloudflare Tunnel: no inbound ports; optional Cloudflare Access login in front.',
}
MODE_ORDER = ['none', 'port_forward', 'tunnel']
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
    'Account > Access: Service Tokens > Edit   (only if you want a service token)',
]


def choose_mode(ctx):
    dflt = default_mode(ctx.repo_dir)
    ctx.ui.say('')
    ctx.ui.say('Remote access: how will you reach RWSOLS from outside your home network?')
    if dflt == 'port_forward':
        ctx.ui.say('(An existing port-forwarding based install was detected, so that is the default.)')
    i, _ = ctx.ui.choose('Choose a remote access mode.', [MODE_LABELS[m] for m in MODE_ORDER], MODE_ORDER.index(dflt) + 1)
    return MODE_ORDER[i - 1]


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


def read_settings(cfg):
    return dict((k, cfg.get(k, v)) for k, v in DEFAULTS.items())


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
        ui.say('')
        ui.say('--- Cloudflare Tunnel ---')
        ui.say('cloudflared makes an outbound-only connection to Cloudflare. Remove any router port forwards for RWSOLS.')
        prev = self.st.get('tunnel', {})
        while True:
            h = ui.ask('Public hostname for RWSOLS (e.g. wol.example.com)', self.new['TUNNEL_HOSTNAME'] or prev.get('hostname'))
            try:
                h = validate_hostname(h)
                if '.' not in h or h.startswith('[') or re.fullmatch(r'[0-9.]+', h):
                    raise ValueError('use a full domain name that is on Cloudflare')
                break
            except ValueError as e:
                ui.warn(str(e))
        self.new['TUNNEL_HOSTNAME'] = h
        while True:
            port = ui.ask('Local port for the loopback Apache listener cloudflared connects to', str(self.new['TUNNEL_ORIGIN_PORT']))
            if port.isdigit() and 1024 <= int(port) <= 65535:
                if tunnel.check_origin_port(self.ctx, self.st, self.new['TUNNEL_ORIGIN_BIND'], int(port)):
                    self.new['TUNNEL_ORIGIN_PORT'] = int(port)
                    break
                ui.warn('Port %s on %s is already in use. Choose another.' % (port, self.new['TUNNEL_ORIGIN_BIND']))
            else:
                ui.warn('Enter a port between 1024 and 65535.')
        hosts = ddclient_hosts(self.ctx.runner)
        if tunnel.ddns_overlap(h, '\n'.join([', '.join(hosts)])):
            ui.warn('%s is also in your ddclient host list. The tunnel owns that DNS record (a proxied CNAME); '
                    'ddclient would keep overwriting it with your IP. Remove it from /etc/ddclient.conf.' % h)
        i, _ = ui.choose('How should the tunnel be set up?', [
            'Automatic: I will provide a Cloudflare API token and the wizard creates the tunnel, DNS record, and service.',
            'Manual: I created the tunnel in the Cloudflare dashboard and will paste its tunnel token.'],
            1 if prev.get('method', 'automatic') == 'automatic' else 2)
        self.method = 'automatic' if i == 1 else 'manual'
        if self.method == 'automatic':
            self.tunnel_name = ui.ask('Tunnel name', prev.get('name') or 'rwsols')
        else:
            ui.say('')
            ui.say('In the Cloudflare Zero Trust dashboard (Networks > Tunnels), create a Cloudflared tunnel and add a public hostname:')
            ui.say('    Hostname: %s' % h)
            ui.say('    Service:  HTTP  %s:%d' % (self.new['TUNNEL_ORIGIN_BIND'], self.new['TUNNEL_ORIGIN_PORT']))
            ui.say('Then copy the tunnel token (the long string after "cloudflared ... --token").')

    def _plan_access(self):
        ui = self.ui
        ui.say('')
        ui.say('--- Cloudflare Access (optional) ---')
        ui.say('Access puts a login (email one-time code, SSO, ...) in front of RWSOLS at Cloudflare\'s edge,')
        ui.say('so strangers never reach the Pi. RWSOLS also verifies the Access token and refuses tunnel requests without it.')
        enabled_before = bool(self.cur['ACCESS_AUD'])
        if not ui.confirm('Enable Cloudflare Access for %s?' % self.new['TUNNEL_HOSTNAME'], enabled_before):
            self.access_mode = 'off'
            self.new.update(ACCESS_TEAM_DOMAIN=None, ACCESS_AUD=None)
            return
        if self.method == 'automatic':
            self.access_mode = 'automatic'
            ui.say('Who may log in? (An Allow policy is created from these.)')
            self.identities = access.ask_identities(ui)
            self.want_service_token = ui.confirm('Also create a service token for non-browser clients (scripts, apps)?', False)
        else:
            self.access_mode = 'manual'
            team, aud = access.ask_manual(ui)
            self.new.update(ACCESS_TEAM_DOMAIN=team, ACCESS_AUD=aud)

    def _plan_allowlist(self):
        ui = self.ui
        ui.say('')
        ui.say('--- Hostname allowlist (optional) ---')
        ui.say('A DNS-rebinding attack makes a malicious website resolve to your Pi so a browser will talk to it.')
        ui.say('An allowlist makes RWSOLS (Apache) answer only to host names you list; everything else gets an empty HTTP 421.')
        cands = []
        if self.mode == 'tunnel':
            cands.append(self.new['TUNNEL_HOSTNAME'])
        cands += self.suggested
        try:
            cands.append(validate_hostname(self.ctx.hostname_fn().split('.')[0] + '.local'))
        except ValueError:
            pass
        cands += list(self.cur['ALLOWED_HOSTS'])
        if self.lan_ip and ui.confirm('Also allow browsing by LAN IP %s?' % self.lan_ip, False):
            cands.append(self.lan_ip)
        suggestion = []
        for c in cands:
            try:
                v = validate_hostname(c)
            except ValueError:
                continue
            if v not in suggestion:
                suggestion.append(v)
        had = bool(self.cur['ALLOWED_HOSTS'])
        i, _ = ui.choose('Enable the hostname allowlist?', ['Yes, enable it (recommended).', 'No, accept any Host name (current default behavior).'],
                         1 if had else 2)
        if i == 2:
            self.new.update(ALLOWED_HOSTS=[], ALLOW_LOCALHOST=False)
            return
        ui.warn('WARNING: once enabled, browsing RWSOLS by any name or IP that is NOT on the list stops working.')
        while True:
            raw = ui.ask('Allowed host names (comma separated)', ', '.join(suggestion))
            try:
                hosts = [validate_hostname(x) for x in raw.split(',') if x.strip()]
                if not hosts:
                    raise ValueError('enter at least one host name')
                break
            except ValueError as e:
                ui.warn(str(e))
        if self.mode == 'tunnel' and self.new['TUNNEL_HOSTNAME'] not in hosts:
            hosts.append(self.new['TUNNEL_HOSTNAME'])
            ui.say('Added the tunnel hostname %s to the list.' % self.new['TUNNEL_HOSTNAME'])
        self.new['ALLOWED_HOSTS'] = list(dict.fromkeys(hosts))
        self.new['ALLOW_LOCALHOST'] = ui.confirm('Also allow localhost / 127.0.0.1 / [::1]?', bool(self.cur['ALLOW_LOCALHOST']))
        self.new['LOG_HOST_REJECTIONS'] = ui.confirm('Log rejected requests to syslog (rate limited)?', bool(self.cur['LOG_HOST_REJECTIONS']))

    # ----- cloud phase -----

    def _get_api(self):
        if self._api:
            return self._api
        ui = self.ui
        token = self.ctx.env.pop('CLOUDFLARE_API_TOKEN', None)
        if not token:
            ui.say('')
            ui.say('Create an API token at https://dash.cloudflare.com/profile/api-tokens with at least these permissions:')
            for s in TOKEN_SCOPES:
                ui.say('    ' + s)
            if self.access_mode == 'automatic' or self.st.get('access', {}).get('app_id'):
                ui.say('  and for Cloudflare Access:')
                for s in ACCESS_SCOPES:
                    ui.say('    ' + s)
            ui.say('(Or set the CLOUDFLARE_API_TOKEN environment variable and re-run.) The token is kept in memory only.')
            token = ui.ask_secret('Cloudflare API token')
        if not token:
            raise SetupError('No API token provided.')
        self._api = CloudflareAPI(token, self.ctx.http, ui, self.ctx.dry_run)
        return self._api

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
            team, aud, tok = ap.run(self.identities[0], self.identities[1], self.want_service_token)
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
            for label, ok, detail in tunnel.verify(ctx, n['TUNNEL_HOSTNAME'], n['TUNNEL_ORIGIN_BIND'], n['TUNNEL_ORIGIN_PORT'],
                                                   access_on, self._api, t.get('account_id'), t.get('id')):
                ui.say('  [%s] %s%s' % ('ok' if ok else 'FAIL', label, '' if ok else ' (%s)' % detail))
                if not ok:
                    failures.append(label)
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
        if self.mode == 'tunnel':
            ui.say('')
            ui.say('Cloudflare Tunnel is set up: https://%s' % self.new['TUNNEL_HOSTNAME'])
            ui.warn('Remove any router port forwards (80/443) that point at RWSOLS; they are no longer needed.')
            if self.ctx.runner.path_exists('/etc/apache2/sites-enabled/000-default-le-ssl.conf'):
                ui.warn('A Let\'s Encrypt certificate issued via certbot --apache renews over HTTP-01 on port 80, which will stop '
                        'working once port 80 is closed. Switch to a DNS-01 challenge (e.g. a certbot DNS plugin) or use a self-signed LAN certificate.')
            if failures:
                ui.warn('Some checks failed: ' + '; '.join(failures))
        if self.new['ALLOWED_HOSTS']:
            ui.say('Allowed hosts: ' + ', '.join(self.new['ALLOWED_HOSTS']))
            ui.say('TLS note: Apache still completes the TLS handshake for unlisted SNI names; the allowlist rejects them at the HTTP layer.')

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
        for ln in lines:
            ui.say('  - ' + ln)
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
    w = RemoteAccessWizard(ctx, mode, suggested_hosts or ddclient_hosts(ctx.runner), lan_ip, copy_webroot)
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
