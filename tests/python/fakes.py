"""Test doubles: fake command runner, fake Cloudflare API transport, scripted UI. No network, no root."""
import base64
import json
import os
import shutil
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rwsols_setup.dnscheck import DOH_URL  # noqa: E402
from rwsols_setup.runtime import CmdResult, Context, HttpResponse  # noqa: E402

API_TOKEN = 'TESTAPITOKEN-0123456789abcdef'
TUNNEL_SECRET = 'TUNNELSECRET-zzzzzzzz'
BASE = 'https://api.cloudflare.com/client/v4'
FAKE_CONFIG = """<?php
// sample
$USE_HTTPS = false;
$APPROVED_HASH = NULL; // keep me
$MAX_PINGS = 15;
?>
"""


def make_tunnel_token(account, tunnel_id):
    return base64.b64encode(json.dumps({'a': account, 't': tunnel_id, 's': TUNNEL_SECRET}).encode()).decode()


class FakeRunner(object):
    def __init__(self, machine='aarch64', deb_arch='arm64', installed=False):
        self.calls = []          # dicts: argv, input, sudo, read_only
        self.files = {}          # path -> (bytes, mode)
        self.enabled = set()
        self.active = set()
        self.machine, self.deb_arch, self.installed = machine, deb_arch, installed
        self.configtest_ok = True
        self.packages = set()    # installed apt packages (only the ones tests care about)
        self.apt_ok = True
        self.groups = {'www-data'}
        self.stat_out = 'root:root 644'
        self.found = []          # what `find` prints

    def apt_config(self):
        """`apt-config dump` output derived from the apt.conf.d files written so far."""
        lines = []
        for path in sorted(self.files):
            if path.startswith('/etc/apt/apt.conf.d/'):
                for ln in self.files[path][0].decode('utf8').splitlines():
                    if '::' in ln and ln.rstrip().endswith('";') and not ln.lstrip().startswith(('//', '"')):
                        lines.append(ln.strip())
        return '\n'.join(lines) + '\n'

    @property
    def mutations(self):
        return [c for c in self.calls if not c['read_only']]

    def argvs(self):
        return [c['argv'] for c in self.calls]

    def run(self, argv, input=None, sudo=False, check=True, read_only=False):
        argv = list(argv)
        self.calls.append({'argv': argv, 'input': input, 'sudo': sudo, 'read_only': read_only})
        out = ''
        rc = 0
        if argv[:2] == ['uname', '-m']:
            out = self.machine
        elif argv[:2] == ['dpkg', '--print-architecture']:
            out = self.deb_arch
        elif argv[:2] == ['which', 'cloudflared']:
            rc = 0 if self.installed else 1
        elif argv[:2] == ['apt-get', 'install'] and 'cloudflared' in argv:
            self.installed = True
        elif argv[:2] == ['apt-get', 'remove'] and 'cloudflared' in argv:
            self.installed = False
        elif argv[:2] == ['systemctl', 'is-active']:
            out = 'active' if argv[2] in self.active else 'inactive'
            rc = 0 if argv[2] in self.active else 3
        elif argv[:2] == ['systemctl', 'enable'] or argv[:2] == ['systemctl', 'restart']:
            self.active.add(argv[-1])
        elif argv[:2] == ['systemctl', 'disable']:
            self.active.discard(argv[-1])
        elif argv[0] in ('a2enconf', 'a2ensite'):
            kind = 'conf' if argv[0] == 'a2enconf' else 'sites'
            self.enabled.add('/etc/apache2/%s-enabled/%s.conf' % (kind, argv[1]))
        elif argv[0] in ('a2disconf', 'a2dissite'):
            kind = 'conf' if argv[0] == 'a2disconf' else 'sites'
            self.enabled.discard('/etc/apache2/%s-enabled/%s.conf' % (kind, argv[1]))
        elif argv[:2] == ['apache2ctl', 'configtest']:
            if not self.configtest_ok:
                rc, out = 1, 'Syntax error'
        elif argv[:1] == ['getent']:
            rc = 0 if argv[-1] in self.groups else 2
        elif argv[:1] == ['stat']:
            rc = 0 if argv[-1] in self.files else 1
            out = self.stat_out
        elif argv[:1] == ['find']:
            out = ''.join(p + chr(10) for p in self.found)
        elif argv[:1] == ['dpkg-query']:
            pkg = argv[-1]
            rc = 0 if pkg in self.packages else 1
            out = 'install ok installed' if pkg in self.packages else ''
        elif argv[:2] == ['apt-config', 'dump']:
            out = self.apt_config()
        elif 'apt-get' in argv and 'install' in argv and not self.apt_ok:
            rc = 100
        elif 'apt-get' in argv and 'install' in argv:
            self.packages.add(argv[-1])
        elif argv[:1] == ['tee']:
            self.files[argv[1]] = (input.encode() if isinstance(input, str) else input, None)
        if check and rc != 0:
            from rwsols_setup.errors import CommandError
            raise CommandError('failed: ' + ' '.join(argv))
        return CmdResult(rc, out, '')

    def read_file(self, path):
        f = self.files.get(path)
        return f[0] if f else None

    def path_exists(self, path):
        return path in self.files or path in self.enabled

    def write_file(self, path, content, mode='0644'):
        data = content.encode('utf8') if isinstance(content, str) else content
        self.calls.append({'argv': ['<write_file>', path, mode], 'input': data, 'sudo': True, 'read_only': False})
        self.files[path] = (data, mode)

    def remove_file(self, path):
        self.calls.append({'argv': ['<remove_file>', path], 'input': None, 'sudo': True, 'read_only': False})
        self.files.pop(path, None)


class FakeCloudflare(object):
    """In-memory Cloudflare. Records every call; unknown routes fail the test."""

    def __init__(self, zone='example.com', account='acct111', auth_domain='example.cloudflareaccess.com'):
        self.calls = []
        self.zones = {zone: {'id': 'zone111', 'name': zone, 'account': {'id': account}}}
        self.account = account
        self.tunnels = []
        self.configs = {}
        self.dns = []
        self.apps = []
        self.policies = []
        self.service_tokens = []
        self.auth_domain = auth_domain
        self.ns = {zone: ['ada.ns.cloudflare.com', 'bob.ns.cloudflare.com'],   # public NS records per zone
                   'elsewhere.example.net': ['ns1.dns-host.example', 'ns2.dns-host.example']}
        self.doh_calls = []
        self.web = {}            # url -> (status, body) overrides for non-API fetches
        self.web_calls = []
        self.fail_with = None    # (status, message) to force an API failure
        self._n = 0

    @property
    def mutations(self):
        return [c for c in self.calls if c[0] != 'GET']

    def _id(self, prefix):
        self._n += 1
        return '%s-%04d' % (prefix, self._n)

    def request(self, method, url, headers=None, body=None, timeout=20, follow_redirects=True):
        if url.startswith(DOH_URL):
            return self._doh(parse_qs(urlparse(url).query)['name'][0])
        if not url.startswith(BASE):
            self.web_calls.append((method, url, dict(headers or {})))
            if url in self.web:
                st, b = self.web[url]
                return HttpResponse(st, b, {})
            if url.endswith('.gpg'):
                return HttpResponse(200, b'KEYDATA', {})
            return HttpResponse(200, b'hello?', {})
        u = urlparse(url)
        path, q = u.path[len('/client/v4'):], dict((k, v[0]) for k, v in parse_qs(u.query).items())
        data = json.loads(body.decode()) if body else None
        assert headers.get('Authorization') == 'Bearer ' + API_TOKEN, 'missing/incorrect bearer token'
        self.calls.append((method, path, data))
        if self.fail_with:
            return self._err(*self.fail_with)
        res = self._route(method, path, q, data)
        if isinstance(res, HttpResponse):
            return res
        return HttpResponse(200, json.dumps({'success': True, 'errors': [], 'result': res}).encode(), {})

    def _doh(self, name):
        """Public DNS-over-HTTPS answer for an NS query. self.ns maps zone -> nameservers; None = lookup fails."""
        self.doh_calls.append(name)
        if self.ns is None:
            return HttpResponse(503, b'', {})
        name = name.rstrip('.').lower()
        for zone, servers in self.ns.items():
            if name == zone:
                return HttpResponse(200, json.dumps({'Status': 0, 'Answer': [
                    {'name': zone + '.', 'type': 2, 'data': s + '.'} for s in servers]}).encode(), {})
            if name.endswith('.' + zone):
                return HttpResponse(200, json.dumps({'Status': 3, 'Authority': [
                    {'name': zone + '.', 'type': 6, 'data': 'ns. hostmaster. 1 2 3 4 5'}]}).encode(), {})
        return HttpResponse(200, json.dumps({'Status': 3, 'Authority': [
            {'name': name.rsplit('.', 1)[-1] + '.', 'type': 6, 'data': 'a. b. 1 2 3 4 5'}]}).encode(), {})

    def _err(self, status, msg):
        return HttpResponse(status, json.dumps({'success': False, 'errors': [{'code': 1000, 'message': msg}], 'result': None}).encode(), {})

    def _route(self, m, p, q, d):
        parts = p.strip('/').split('/')
        A = self.account
        if p == '/zones' and m == 'GET':
            z = self.zones.get(q.get('name'))
            return [z] if z else []
        if parts[:3] == ['accounts', A, 'cfd_tunnel']:
            rest = parts[3:]
            if not rest:
                if m == 'GET':
                    return [t for t in self.tunnels if t['name'] == q.get('name') and not t.get('deleted_at')]
                t = {'id': self._id('tun'), 'name': d['name'], 'config_src': d['config_src'], 'status': 'inactive', 'deleted_at': None}
                self.tunnels.append(t)
                return t
            tid = rest[0]
            t = next(x for x in self.tunnels if x['id'] == tid)
            if rest[1:] == ['configurations']:
                if m == 'GET':
                    return {'tunnel_id': tid, 'config': self.configs.get(tid)}
                self.configs[tid] = d['config']
                t['status'] = 'healthy'
                return {'tunnel_id': tid, 'config': d['config']}
            if rest[1:] == ['token']:
                return make_tunnel_token(A, tid)
            if rest[1:] == ['connections'] and m == 'DELETE':
                return {}
            if len(rest) == 1 and m == 'GET':
                return t
            if len(rest) == 1 and m == 'DELETE':
                self.tunnels.remove(t)
                return t
        if parts[0] == 'zones' and parts[2:3] == ['dns_records']:
            rest = parts[3:]
            if not rest and m == 'GET':
                return [r for r in self.dns if r['name'] == q.get('name')]
            if not rest and m == 'POST':
                r = dict(d, id=self._id('dns'))
                self.dns.append(r)
                return r
            r = next(x for x in self.dns if x['id'] == rest[0])
            if m == 'GET':
                return r
            if m == 'PATCH':
                r.update(d)
                return r
            if m == 'DELETE':
                self.dns.remove(r)
                return r
        if parts[:3] == ['accounts', A, 'access']:
            kind, rest = parts[3], parts[4:]
            if kind == 'organizations':
                return {'auth_domain': self.auth_domain}
            coll = {'apps': self.apps, 'policies': self.policies, 'service_tokens': self.service_tokens}[kind]
            if not rest and m == 'GET':
                return list(coll)
            if not rest and m == 'POST':
                o = dict(d, id=self._id(kind))
                if kind == 'apps':
                    o['aud'] = ('%064x' % (len(coll) + 1))
                if kind == 'service_tokens':
                    o.update(client_id='cid.access', client_secret='CLIENTSECRET-xyz')
                coll.append(o)
                return o
            o = next(x for x in coll if x['id'] == rest[0])
            if m == 'PUT':
                o.update(d)
                return o
            if m == 'DELETE':
                coll.remove(o)
                return o
        raise AssertionError('unexpected API call %s %s' % (m, p))


class FakeResolver(object):
    """Injectable DNS: reverse = {ip: name}, forward = {name: [ips]}. Never raises, like the real one."""

    def __init__(self, reverse=None, forward=None):
        self.rev = dict(reverse or {})
        self.fwd = dict(forward or {})

    def reverse(self, ip):
        return self.rev.get(ip)

    def forward(self, name):
        return list(self.fwd.get(name, []))


class ScriptedUI(object):
    """Answers prompts by substring rules; unmatched prompts take their default.
    choose_many rules: a list of values = exactly those ticked (locked entries are always included;
    values that are not options count as added by the user). No rule = the default ticks."""

    def __init__(self, rules=None, secrets=None):
        self.rules = list(rules or [])
        self.secrets = list(secrets or [])
        self.out = []
        self.many_calls = []     # dicts: query, options, result

    def _match(self, prompt):
        for sub, ans in self.rules:
            if sub in prompt:
                return True, ans
        return False, None

    def say(self, text=''):
        self.out.append(text)

    warn = error = success = info = hint = heading = say

    def pause(self, text=''):
        self.out.append(text)

    def explain(self, title, lines, style='accent'):
        self.out.append(title)
        self.out.extend(ln[0] if isinstance(ln, tuple) else ln for ln in lines)

    def summary(self, title, lines, warnings=()):
        self.explain(title, list(lines) + list(warnings))

    def checklist(self, title, rows):
        self.out.append(title)
        for label, ok, detail in rows:
            self.out.append('[ok] %s' % label if ok else '[FAIL] %s (%s)' % (label, detail))

    def ask(self, prompt, default=None, validator=None):
        self.out.append(prompt)
        for _ in range(50):
            hit, ans = self._match(prompt)
            if hit:
                if isinstance(ans, list):
                    ans = ans.pop(0) if len(ans) > 1 else ans[0]
                raw = ans
            else:
                raw = default or ''
            if validator is None:
                return raw
            try:
                return validator(raw)
            except ValueError as e:
                self.warn(str(e))
        raise AssertionError('ScriptedUI.ask kept failing validation for %r' % prompt)

    def ask_secret(self, prompt, strip=True):
        self.out.append(prompt)
        return self.secrets.pop(0)

    def confirm(self, prompt, default=False):
        self.out.append(prompt)
        hit, ans = self._match(prompt)
        return ans if hit else default

    def choose(self, query, options, default=None):
        self.out.append(query)
        hit, ans = self._match(query)
        i = ans if hit else (default or 1)
        return i, options[i - 1]

    def choose_many(self, query, options, validator=None, min_selected=1, add_prompt='Name to add'):
        from rwsols_setup.ui import as_option
        self.out.append(query)
        opts = [as_option(o) for o in options]
        hit, ans = self._match(query)
        if hit:
            wanted = [validator(v) if validator else v for v in ans]
            result = [o.value for o in opts if o.locked or o.value in wanted]
            result += [v for v in wanted if v not in [o.value for o in opts]]
        else:
            result = [o.value for o in opts if o.locked or o.checked]
        self.many_calls.append({'query': query, 'options': opts, 'result': result})
        return result

    def text(self):
        return chr(10).join(self.out)


class Env(object):
    """A temp repo dir + context with all fakes wired up."""

    def __init__(self, rules=None, secrets=None, dry_run=False, force=False, runner=None, cf=None, config=FAKE_CONFIG, token=True,
                 resolver=None):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, 'www', 'html'))
        self.config_path = os.path.join(self.dir, 'www', 'html', 'config.php')
        with open(self.config_path, 'w', newline='') as f:
            f.write(config)
        self.runner = runner or FakeRunner()
        self.cf = cf or FakeCloudflare()
        self.ui = ScriptedUI(rules, secrets)
        self.dry_run, self.force = dry_run, force
        self.token = token
        self.resolver = resolver or FakeResolver()
        self.make_ctx()

    def make_ctx(self, rules=None, secrets=None):
        if rules is not None:
            self.ui = ScriptedUI(rules, secrets)
        env = {'CLOUDFLARE_API_TOKEN': API_TOKEN} if self.token else {}
        self.ctx = Context(self.runner, self.cf, self.ui, self.dir, dry_run=self.dry_run, force=self.force, env=env,
                           sleep=lambda s: None, port_free=lambda b, p: True, hostname_fn=lambda: 'raspberrypi', resolver=self.resolver)
        return self.ctx

    def config(self):
        with open(self.config_path, newline='') as f:
            return f.read()

    def state_text(self):
        p = os.path.join(self.dir, '.rwsols_remote_access.json')
        if not os.path.exists(p):
            return ''
        with open(p) as f:
            return f.read()

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def everything_observable(self):
        """All text that must never contain secrets: argv, UI output, state file."""
        argv = ' '.join(' '.join(c['argv']) for c in self.runner.calls)
        return argv + '\n' + self.ui.text() + '\n' + self.state_text()
