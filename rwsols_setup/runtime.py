"""Injectable seams: command runner, HTTP transport, UI, and the Context that bundles them."""
import getpass
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections import namedtuple

from .errors import CommandError, NetworkError

CmdResult = namedtuple('CmdResult', ['returncode', 'stdout', 'stderr'])
HttpResponse = namedtuple('HttpResponse', ['status', 'body', 'headers'])


def _to_bytes(data):
    return data.encode('utf8') if isinstance(data, str) else data


class SystemRunner(object):
    """Runs commands (with sudo when not root). Secrets must only ever be passed as `input`
    (stdin), never inside argv: argv is visible to every local user via /proc and `ps`."""

    def __init__(self, use_sudo=None):
        if use_sudo is None:
            use_sudo = not (hasattr(os, 'geteuid') and os.geteuid() == 0)
        self.use_sudo = use_sudo

    def run(self, argv, input=None, sudo=False, check=True, read_only=False):
        argv = list(argv)
        if sudo and self.use_sudo:
            argv = ['sudo'] + argv
        try:
            p = subprocess.run(argv, input=_to_bytes(input) if input is not None else None,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            if check:
                raise CommandError('Could not run %s: %s' % (argv[0], e))
            return CmdResult(127, '', str(e))
        res = CmdResult(p.returncode, p.stdout.decode('utf8', 'replace'), p.stderr.decode('utf8', 'replace'))
        if check and res.returncode != 0:
            raise CommandError('Command failed (%d): %s\n%s' % (res.returncode, ' '.join(argv), res.stderr.strip()))
        return res

    def read_file(self, path):
        res = self.run(['cat', path], sudo=True, check=False, read_only=True)
        return res.stdout.encode('utf8') if res.returncode == 0 else None

    def path_exists(self, path):
        return self.run(['test', '-e', path], sudo=True, check=False, read_only=True).returncode == 0

    def write_file(self, path, content, mode='0644'):
        # Content goes in through stdin; umask keeps a secret file private from the first byte.
        self.run(['sh', '-c', 'umask 077; cat > "$1"', 'sh', path], input=content, sudo=True)
        self.run(['chown', 'root:root', path], sudo=True)
        self.run(['chmod', mode, path], sudo=True)

    def remove_file(self, path):
        self.run(['rm', '-f', path], sudo=True)


class DryRunRunner(object):
    """Delegates reads, prints (and skips) everything that would change the system."""

    def __init__(self, inner, ui):
        self.inner = inner
        self.ui = ui

    def run(self, argv, input=None, sudo=False, check=True, read_only=False):
        if read_only:
            return self.inner.run(argv, input=input, sudo=sudo, check=check, read_only=True)
        self.ui.say('[dry-run] would run: ' + ' '.join(argv))
        return CmdResult(0, '', '')

    def read_file(self, path):
        return self.inner.read_file(path)

    def path_exists(self, path):
        return self.inner.path_exists(path)

    def write_file(self, path, content, mode='0644'):
        self.ui.say('[dry-run] would write %s (mode %s)' % (path, mode))

    def remove_file(self, path):
        self.ui.say('[dry-run] would remove %s' % path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class UrllibTransport(object):
    def request(self, method, url, headers=None, body=None, timeout=20, follow_redirects=True):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        handlers = [] if follow_redirects else [_NoRedirect()]
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(req, timeout=timeout) as r:
                return HttpResponse(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            return HttpResponse(e.code, e.read(), dict(e.headers))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise NetworkError('Network error for %s %s: %s' % (method, url.split('?')[0], getattr(e, 'reason', e)))


class ConsoleUI(object):
    def say(self, text=''):
        print(text)

    def warn(self, text):
        print('\033[0;33m' + text + '\033[0m')

    def ask(self, prompt, default=None):
        v = input(prompt + (' [%s]' % default if default else '') + ': ').strip()
        return v if v else (default or '')

    def ask_secret(self, prompt):
        return getpass.getpass(prompt + ': ').strip()

    def confirm(self, prompt, default=False):
        while True:
            v = input('%s [%s]: ' % (prompt, 'Y/n' if default else 'y/N')).strip().lower()
            if v == '':
                return default
            if v in ('y', 'yes'):
                return True
            if v in ('n', 'no'):
                return False

    def choose(self, query, options, default=None):
        """Returns (1-based index, option). Enter selects `default` (1-based) when given."""
        while True:
            print(query)
            for i, o in enumerate(options):
                print((' %d: %s' if i >= 9 else ' %d:  %s') % (i + 1, o) + ('  (default)' if default == i + 1 else ''))
            v = input('Choose a number from the list (1-%d): ' % len(options)).strip()
            if v == '' and default:
                return default, options[default - 1]
            if v.isdigit() and 1 <= int(v) <= len(options):
                return int(v), options[int(v) - 1]
            self.warn('Please choose a number from the list!\n')


def port_is_free(bind, port):
    s = socket.socket(socket.AF_INET6 if ':' in bind else socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((bind.strip('[]'), port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Context(object):
    def __init__(self, runner, http, ui, repo_dir, dry_run=False, force=False, env=None,
                 sleep=time.sleep, port_free=port_is_free, hostname_fn=socket.gethostname):
        self.repo_dir = repo_dir
        self.ui = ui
        self.http = http
        self.dry_run = dry_run
        self.force = force
        self.env = os.environ if env is None else env
        self.sleep = sleep
        self.port_free = port_free
        self.hostname_fn = hostname_fn
        self.runner = DryRunRunner(runner, ui) if dry_run else runner
        self.config_path = os.path.join(repo_dir, 'www', 'html', 'config.php')
        self.state_path = os.path.join(repo_dir, '.rwsols_remote_access.json')
