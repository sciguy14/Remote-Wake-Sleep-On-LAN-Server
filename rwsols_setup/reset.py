"""Start over: put the RWSOLS settings back to their defaults so setup can be gone through from the top.

Only things this script wrote are touched. Installed packages, certificates, automatic updates and anything
in the user's Cloudflare account are left alone; the sections of setup.py deal with those when they run."""
import glob
import os
import shutil
import time

from . import apache, tunnel

CONFIRM_QUERY = 'Reset the RWSOLS settings to their defaults and start setup over?'
DDNS_QUERY = 'Also remove the dynamic DNS configuration on this Pi (/etc/ddclient.conf)?'
DDCLIENT_CONF = '/etc/ddclient.conf'
DDCLIENT_BACKUP = '/etc/ddclient.conf.before-rwsols-reset'
SERVED_CONFIG = '/var/www/html/config.php'
BACKUP_PREFIX = '.config.php.before-reset-'
PROGRESS_GLOB = '._[0-9][0-9]_*'


def _has_cloud_objects(st):
    t = (st or {}).get('tunnel') or {}
    return bool(t.get('id') or t.get('created_tunnel') or t.get('dns_created') or t.get('hostname')
                or ((st or {}).get('access') or {}).get('app_created'))


def run(ctx, st=None):
    """Explain, confirm, reset. Returns True when the reset was done, False when the user backed out."""
    ui, runner = ctx.ui, ctx.runner
    sample = os.path.join(os.path.dirname(ctx.config_path), 'config_sample.php')
    lines = [
        'This puts RWSOLS back to how it is right after downloading it, so you can go through setup from the top:',
        '    config.php goes back to the defaults: the passphrase, the list of computers, the allowed hosts and the '
        'remote access settings are cleared. Nobody can log in to RWSOLS until you finish setup again.',
        '    The record of which setup sections are done is cleared.',
        '    The Cloudflare Tunnel service on this Pi and the Apache settings this script added are removed.',
        'Kept: installed software, certificates, automatic security updates, and your router settings. The sections that '
        'cover them ask again what you want.',
        'Your current config.php is saved first, in this folder, as a hidden file whose name starts with "%s".' % BACKUP_PREFIX,
    ]
    warnings = []
    if _has_cloud_objects(st):
        warnings.append('The tunnel, DNS record and Access application in your Cloudflare account are NOT removed by this. Setup '
                        'reuses them if you choose the same hostname again. To delete them instead, answer No here and run: '
                        'python3 setup.py --remove-tunnel')
    ui.explain('Start over', lines + warnings, style='warn')
    if not ui.confirm(CONFIRM_QUERY, False):
        ui.say('Nothing was changed.')
        return False
    drop_ddns = bool(runner.read_file(DDCLIENT_CONF)) and ui.confirm(DDNS_QUERY, False)
    if ctx.dry_run:
        ui.say('[dry-run] would reset the settings; nothing was changed.')
        return False

    done = []
    # 1. Keep a copy of the current settings, readable only by this user.
    if os.path.exists(ctx.config_path):
        backup = os.path.join(ctx.repo_dir, BACKUP_PREFIX + time.strftime('%Y-%m-%d_%H-%M-%S'))
        shutil.copyfile(ctx.config_path, backup)
        os.chmod(backup, 0o600)
        done.append('Saved the old config.php as %s' % os.path.basename(backup))
    # 2. The pieces this script installed outside this folder.
    if tunnel.remove_service(ctx):
        done.append('Removed the Cloudflare Tunnel service from this Pi')
    if apache.remove_tunnel_site(ctx):
        done.append('Removed the tunnel listener from Apache')
    if apache.apply_allowed_hosts(ctx, None):
        done.append('Removed the hostname allowlist from Apache')
    if drop_ddns:
        runner.run(['systemctl', 'disable', '--now', 'ddclient'], sudo=True, check=False)
        runner.run(['mv', '-f', DDCLIENT_CONF, DDCLIENT_BACKUP], sudo=True)
        done.append('Stopped dynamic DNS and moved its configuration to %s' % DDCLIENT_BACKUP)
    # 3. Default settings, here and in the copy the web server uses.
    shutil.copyfile(sample, ctx.config_path)
    if runner.path_exists(SERVED_CONFIG):
        # cp over an existing file keeps its owner and mode (root, web server group, not world-readable).
        runner.run(['cp', sample, SERVED_CONFIG], sudo=True)
    done.append('Put config.php back to the defaults')
    # 4. Setup progress.
    for path in glob.glob(os.path.join(ctx.repo_dir, PROGRESS_GLOB)) + [ctx.state_path]:
        if os.path.isfile(path):
            os.remove(path)
    done.append('Cleared the record of finished setup sections')
    ui.explain('Reset done', ['- ' + d for d in done], style='success')
    return True
