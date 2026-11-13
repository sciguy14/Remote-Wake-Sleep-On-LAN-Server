"""The setup script as a checklist of sections: what is done, what is left, and a menu to run or redo one.

Each section is a function supplied by setup.py. This module only knows their order, how to work out their
status from what is on the Pi, and how to walk the user through them."""
import os

from . import hardening
from .phpconfig import ConfigFile
from .state import load_state, new_state, save_state

# key, title shown in the checklist
SECTIONS = [
    ('base', 'Base install'),
    ('remote', 'Remote access'),
    ('ddns', 'Dynamic DNS (optional)'),
    ('https', 'HTTPS on your LAN'),
    ('allowlist', 'Hostname allowlist'),
    ('updates', 'Security updates'),
    ('config', 'Passphrase and computers'),
]
KEYS = [k for k, _ in SECTIONS]
TITLES = dict(SECTIONS)
MODE_NAMES = {'none': 'LAN only', 'port_forward': 'port forwarding', 'tunnel': 'Cloudflare Tunnel'}
LEGACY_DOT_FILES = ('._01_install_prereqs', '._02_ping_permissions', '._09_secure_php')
MENU_QUERY = 'What would you like to do?'
REDO_QUERY = 'Which section do you want to run again?'
RESET_OPTION = 'Start over: reset all settings to the defaults and set everything up again'
LE_SITE = '/etc/apache2/sites-enabled/000-default-le-ssl.conf'
DEFAULT_SITE = '/etc/apache2/sites-available/000-default.conf'
SSL_MOD = '/etc/apache2/mods-enabled/ssl.load'


class Status(object):
    def __init__(self, done, label):
        self.done, self.label = bool(done), label


def _config_value(ctx, name, default=None):
    try:
        return ConfigFile(ctx.config_path).get(name, default)
    except (OSError, ValueError):
        return default


def passphrase_is_set(ctx):
    return bool(_config_value(ctx, 'APPROVED_HASH'))


def _allowed_hosts(ctx):
    return list(_config_value(ctx, 'ALLOWED_HOSTS', []) or [])


def load(ctx):
    """The state, with a `sections` record. An install that predates the checklist and already works is
    recorded as complete, so upgrading never forces anyone back through setup."""
    st = load_state(ctx.state_path) or new_state()
    if 'sections' not in st:
        st['sections'] = {}
        legacy = all(os.path.exists(os.path.join(ctx.repo_dir, f)) for f in LEGACY_DOT_FILES) and passphrase_is_set(ctx)
        if legacy:
            for k in KEYS:
                st['sections'][k] = True
            if not st.get('mode'):
                st['mode'] = 'port_forward'
        save_state(ctx, st)
    return st


def mark_done(ctx, key):
    st = load(ctx)
    st['sections'][key] = True
    save_state(ctx, st)


def unmark(ctx, *keys):
    """Put sections back on the "left to do" list (e.g. after the remote access mode changed)."""
    st = load(ctx)
    for k in keys:
        st['sections'][k] = False
    save_state(ctx, st)


def update(ctx, **values):
    """Merge top-level values (e.g. ddns='pi', https={...}) into the state file."""
    st = load(ctx)
    st.update(values)
    save_state(ctx, st)


def https_label(ctx, st):
    r = ctx.runner
    kind = (st.get('https') or {}).get('kind')
    names = (st.get('https') or {}).get('names') or []
    try:
        if r.path_exists(LE_SITE):
            return 'Let\'s Encrypt certificate (renews over port 80)'
        site = r.read_file(DEFAULT_SITE) or b''
        if b'SSLEngine' in site and r.path_exists(SSL_MOD):
            if kind == 'dns':
                return 'trusted certificate for %s (DNS challenge)' % ', '.join(names)
            return 'self-signed certificate'
    except Exception:
        return 'unknown'
    return 'plain HTTP'


def ddns_label(ctx, st, hosts):
    choice = st.get('ddns')
    if hosts and choice != 'none':
        return 'this Pi updates %s' % ', '.join(hosts)
    return {'router': 'handled by your router or another device', 'none': 'not used'}.get(choice, 'not set up')


def statuses(ctx, ddclient_hosts=()):
    st = load(ctx)
    done = st['sections']
    mode = st.get('mode')
    hosts = _allowed_hosts(ctx)
    try:
        updates_on = hardening.auto_updates_status(ctx.runner)['enabled']
    except Exception:
        updates_on = False
    has_pass = passphrase_is_set(ctx)
    out = {
        'base': Status(done.get('base'), 'done' if done.get('base') else 'not done'),
        'remote': Status(done.get('remote'), MODE_NAMES.get(mode, 'not chosen') if done.get('remote') else 'not chosen'),
        'ddns': Status(done.get('ddns'), ddns_label(ctx, st, list(ddclient_hosts)) if done.get('ddns') else 'not set up'),
        'https': Status(done.get('https'), https_label(ctx, st) if done.get('https') else 'not chosen'),
        'allowlist': Status(done.get('allowlist'), ('on (%d names)' % len(hosts) if hosts else 'off') if done.get('allowlist') else 'not chosen'),
        'updates': Status(done.get('updates') or updates_on or os.path.exists(os.path.join(ctx.repo_dir, '._09b_auto_updates')),
                          'on' if updates_on else 'off'),
        'config': Status(done.get('config') and has_pass, 'passphrase set' if has_pass else 'not done'),
    }
    return out


def show(ctx, sts):
    ui = ctx.ui
    ui.heading('RWSOLS setup')
    width = max(len(t) for t in TITLES.values()) + 2
    for i, key in enumerate(KEYS, 1):
        s = sts[key]
        line = '  %d. %s%s' % (i, TITLES[key].ljust(width), s.label)
        (ui.success if s.done else ui.warn)(line)
    ui.say('')


def run(ctx, runners, ddclient_hosts=lambda: ()):
    """Show the checklist and run sections until the user quits. `runners[key]()` returns True on success.
    Returns True when the user quit normally, False when a section failed."""
    ui = ctx.ui
    first = True
    while True:
        sts = statuses(ctx, ddclient_hosts())
        show(ctx, sts)
        left = [k for k in KEYS if not sts[k].done]
        fresh = first and len(left) == len(KEYS)
        first = False
        walk = True      # going through "what is left" (as opposed to redoing one section)
        if fresh:
            ui.hint('Nothing is set up yet. Setup goes through the sections above in order. You can stop with Ctrl+C at any '
                    'time and run it again: it continues where you left off.')
            ui.pause('Press Enter to start')
            todo = left
        else:
            options, actions = [], []
            if left:
                options.append('Continue with what is left: ' + ', '.join(TITLES[k] for k in left))
                actions.append('left')
            options.append('Run one section again')
            actions.append('redo')
            options.append(RESET_OPTION)
            actions.append('reset')
            options.append('Quit' if left else 'Quit (everything is set up)')
            actions.append('quit')
            default = 1 if left else len(options)
            action = actions[ui.choose(MENU_QUERY, options, default)[0] - 1]
            if action == 'quit':
                return True
            if action == 'reset':
                from . import reset
                from .errors import SetupError
                try:
                    if reset.run(ctx, load_state(ctx.state_path)):
                        first = True       # nothing is set up any more: walk through every section again
                except (SetupError, OSError) as e:
                    ui.error('The reset did not finish: %s' % e)
                    return False
                continue
            if action == 'left':
                todo = left
            else:
                names = ['%d. %s' % (i, TITLES[k]) for i, k in enumerate(KEYS, 1)] + ['Back']
                i = ui.choose(REDO_QUERY, names)[0]
                if i > len(KEYS):
                    continue
                todo = [KEYS[i - 1]]
                walk = False
        for key in todo:
            ui.heading('%s (section %d of %d)' % (TITLES[key], KEYS.index(key) + 1, len(KEYS)))
            if not runners[key]():
                ui.error('The "%s" section did not finish. Fix the problem above and run setup again; it continues from here.'
                         % TITLES[key])
                return False
            mark_done(ctx, key)
        if walk and not [k for k in KEYS if not statuses(ctx, ddclient_hosts())[k].done]:
            show(ctx, statuses(ctx, ddclient_hosts()))
            ui.success('Setup is complete. Run this script again at any time to see this list or to redo a section.')
            return True
