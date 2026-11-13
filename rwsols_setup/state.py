"""Wizard state file (.rwsols_remote_access.json). Never holds secrets. Records what the wizard created."""
import json
import os

MODES = ('none', 'port_forward', 'tunnel')


def new_state():
    return {'version': 1, 'mode': None, 'tunnel': {}, 'access': {}}


def load_state(path):
    try:
        with open(path, 'r', encoding='utf8') as f:
            st = json.load(f)
        if isinstance(st, dict):
            st.setdefault('tunnel', {})
            st.setdefault('access', {})
            return st
    except (OSError, ValueError):
        pass
    return None


def save_state(ctx, st):
    if ctx.dry_run:
        return
    tmp = ctx.state_path + '.tmp'
    with open(tmp, 'w', encoding='utf8') as f:
        json.dump(st, f, indent=2, sort_keys=True)
        f.write('\n')
    os.replace(tmp, ctx.state_path)


def default_mode(repo_dir):
    """New installs default to 'none'; installs predating this feature keep port forwarding."""
    st = load_state(os.path.join(repo_dir, '.rwsols_remote_access.json'))
    if st and st.get('mode') in MODES:
        return st['mode']
    if st and 'sections' in st:
        return 'none'      # the setup checklist has run and no mode was chosen yet: a new install
    if os.path.exists(os.path.join(repo_dir, 'www', 'html', 'config.php')) or \
            os.path.exists(os.path.join(repo_dir, '._04_setup_ddns')):
        return 'port_forward'
    return 'none'
