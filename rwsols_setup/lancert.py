"""A browser-trusted certificate for a LAN name, from Let's Encrypt, using a DNS challenge at Cloudflare.

No inbound port is needed: certbot proves ownership of the name by creating a TXT record through the
Cloudflare API. That needs an API token that stays on the Pi, because renewals happen unattended every
couple of months. It is stored in a file only root can read, and should be able to do nothing but edit DNS
records of the one zone.

Works for a name inside a domain whose DNS is hosted at Cloudflare. A .local name or a bare IP address can
never get a publicly trusted certificate, from anyone."""
import json
from urllib.parse import quote

from . import dnscheck
from .cloudflare_api import CloudflareAPI
from .errors import CommandError, SetupError
from .hosts import validate_hostname

CERT_NAME = 'rwsols-lan'
CREDENTIALS_PATH = '/etc/letsencrypt/rwsols-cloudflare.ini'
LIVE_DIR = '/etc/letsencrypt/live/' + CERT_NAME
APACHE_CERT = '/etc/apache2/ssl/wol.crt'
APACHE_KEY = '/etc/apache2/ssl/wol.key'
TOKEN_PAGE = 'https://dash.cloudflare.com/profile/api-tokens'
TOKEN_SCOPES = ['Zone > DNS > Edit    (restrict it to the one zone)', 'Zone > Zone > Read   (same zone)']
SOURCE_QUERY = 'Which API token should renew the certificate?'
REUSE_OPTION = 'Use the dynamic DNS token that is already stored on this Pi.'
PASTE_OPTION = 'Paste a separate DNS-only token.'
COVER_QUERY = 'What should the certificate cover?'
MAX_TRIES = 5


def token_name(name):
    return 'RWSOLS LAN certificate (%s)' % name


def token_template_url(name):
    perms = json.dumps([{'key': 'dns', 'type': 'edit'}, {'key': 'zone', 'type': 'read'}], separators=(',', ':'))
    return '%s?permissionGroupKeys=%s&accountId=%s&zoneId=all&name=%s' % (
        TOKEN_PAGE, quote(perms, safe=''), quote('*', safe=''), quote(token_name(name), safe=''))


def lan_name(v):
    h = validate_hostname(v)
    if '.' not in h or h.startswith('[') or h.replace('.', '').isdigit():
        raise ValueError('Enter a full name inside your domain, for example rwsols.home.example.com.')
    if h.endswith('.local'):
        raise ValueError('A .local name cannot get a trusted certificate. Use a name inside a domain you own.')
    return h


def wildcard_for(name, zone):
    """'*.home.example.com' for 'pi.home.example.com'. None when the name has no parent inside the zone."""
    parent = name.split('.', 1)[1] if '.' in name else ''
    if not zone or not (parent == zone or parent.endswith('.' + zone)):
        return None
    return '*.' + parent


def ask_cover(ui, name, zone):
    """Ask whether the certificate is for the one name or for every name next to it. Returns the wildcard
    name ('*.home.example.com') or None."""
    wildcard = wildcard_for(name, zone)
    if not wildcard:
        return None
    parent = wildcard[2:]
    ui.explain('One name, or every name under %s?' % parent, [
        'Only this name: the certificate is valid for %s and nothing else. This is all RWSOLS needs.' % name,
        'Every name (a wildcard certificate): one certificate valid for any name directly under %s, such as nas.%s or '
        'printer.%s, and for this Pi. Not covered: %s itself, and names two levels down (a.b.%s).'
        % (parent, parent, parent, parent, parent),
        'A wildcard only helps other devices if you put it to work. The certificate and its private key stay on this Pi '
        '(%s, readable only by root). For another device you either copy both files to it, and again after every '
        'renewal (about every two months), or you run a reverse proxy on this Pi in front of it. This script does '
        'neither for you.' % LIVE_DIR,
        'Whoever holds the private key can pose as any name under %s on your network, so copy it only to devices you '
        'trust.' % parent,
        'The API token needed is the same either way.'])
    i, _ = ui.choose(COVER_QUERY, ['Only %s.' % name, 'Every name directly under %s (%s).' % (parent, wildcard)], 1)
    return wildcard if i == 2 else None


def explain(ui):
    ui.explain('A trusted certificate for a LAN name', [
        'Let\'s Encrypt can issue a certificate that browsers trust for a name inside a domain whose DNS is hosted at '
        'Cloudflare, for example rwsols.home.example.com. It proves you own the name by adding a DNS record, so no router '
        'port has to be open.',
        'What you need: that name must lead to this Pi on your home network. Either your router\'s local DNS already gives '
        'the Pi such a name, or you add a DNS record for it at Cloudflare that holds the Pi\'s LAN address (set to "DNS only").',
        'What it cannot do: a .local name or a bare IP address can never get a trusted certificate. Opening the Pi by one of '
        'those still shows a browser warning.',
        'What it costs: renewals run unattended, so a Cloudflare API token that can edit DNS records stays on this Pi, in a '
        'file only root can read (%s).' % CREDENTIALS_PATH])


def zone_visible(ctx, token, name):
    """True when `token` can see the Cloudflare zone that `name` belongs to (a read-only call)."""
    api = CloudflareAPI(token, ctx.http, ctx.ui, ctx.dry_run)
    labels = name.split('.')
    try:
        for i in range(len(labels) - 1):
            if api.get('/zones', {'name': '.'.join(labels[i:])}):
                return True
    except SetupError:
        return False
    return False


def get_token(ctx, name, ddns_token=None):
    """The token certbot will keep for renewals: the one ddclient already stores, or a pasted DNS-only one.
    It is checked (read-only) before it is accepted. Never printed."""
    ui = ctx.ui
    if ddns_token:
        ui.explain('API token for renewals', [
            'ddclient on this Pi already stores a Cloudflare API token for dynamic DNS. It has exactly the permission a '
            'certificate renewal needs (edit DNS records), so reusing it puts no additional power on this Pi.',
            'A separate token works too, if you would rather keep the two apart.'])
        if ui.choose(SOURCE_QUERY, [REUSE_OPTION, PASTE_OPTION])[0] == 1:
            if zone_visible(ctx, ddns_token, name):
                ui.success('The dynamic DNS token can see the zone of %s. Using it.' % name)
                return ddns_token
            ui.warn('The dynamic DNS token cannot see the Cloudflare zone of %s. Paste a token that can.' % name)
    ui.explain('Cloudflare API token for certificate renewals', [
        'The link below opens Cloudflare\'s Create Token page with the name and these permissions filled in:']
        + ['    ' + x for x in TOKEN_SCOPES]
        + ['Suggested name: %s' % token_name(name),
           'On that page, narrow "Zone Resources" to the one zone, then: Continue to summary > Create Token.',
           'Unlike the tunnel setup token, this one is kept: it is saved in %s (readable only by root) so the certificate '
           'can renew by itself.' % CREDENTIALS_PATH])
    ui.say('Open this link in a browser:')
    ui.link(token_template_url(name))
    for _ in range(MAX_TRIES):
        token = ui.ask_secret('Cloudflare API token (DNS only)')
        if not token:
            raise SetupError('No API token provided.')
        if zone_visible(ctx, token, name):
            ui.success('The token can see the zone of %s.' % name)
            return token
        ui.warn('That token cannot see the Cloudflare zone of %s (it needs Zone > Zone > Read and Zone > DNS > Edit for that zone). '
                'Fix it in the dashboard or paste another.' % name)
    raise SetupError('No usable API token provided.')


def install_plugin(ctx):
    """certbot's Cloudflare DNS plugin, matching how certbot itself is installed (snap or apt)."""
    r = ctx.runner
    if r.run(['snap', 'list', 'certbot'], check=False, read_only=True).returncode == 0:
        r.run(['snap', 'set', 'certbot', 'trust-plugin-with-root=ok'], sudo=True)
        r.run(['snap', 'install', 'certbot-dns-cloudflare'], sudo=True)
    else:
        r.run(['apt-get', 'install', '-y', 'certbot', 'python3-certbot-dns-cloudflare'], sudo=True)


def issue(ctx, names, email, token):
    """Get the certificate and point Apache's certificate paths at it. The token goes to disk only through
    the runner's write_file (stdin, root-only from the first byte); it is never part of a command line."""
    r = ctx.runner
    install_plugin(ctx)
    r.run(['mkdir', '-p', '/etc/letsencrypt', '/etc/apache2/ssl'], sudo=True)
    r.write_file(CREDENTIALS_PATH, 'dns_cloudflare_api_token = %s\n' % token, '0600')
    argv = ['certbot', 'certonly', '--dns-cloudflare', '--dns-cloudflare-credentials', CREDENTIALS_PATH,
            '--dns-cloudflare-propagation-seconds', '30', '--cert-name', CERT_NAME, '--non-interactive', '--agree-tos',
            '--email', email, '--deploy-hook', 'systemctl reload apache2',
            '--renew-with-new-domains']    # a rerun may change what the certificate covers (one name <-> wildcard)
    for n in names:
        argv += ['-d', n]
    try:
        r.run(argv, sudo=True)
    except CommandError as e:
        raise SetupError('certbot could not get the certificate. Nothing about how Apache serves the site was changed.\n%s' % e)
    # Apache's self-signed site config reads these two paths; pointing them at the live certificate lets one
    # config serve both kinds, and a renewal is picked up by the reload in the deploy hook.
    r.run(['ln', '-sfn', LIVE_DIR + '/fullchain.pem', APACHE_CERT], sudo=True)
    r.run(['ln', '-sfn', LIVE_DIR + '/privkey.pem', APACHE_KEY], sudo=True)


def cleanup(ctx):
    """Forget the DNS-challenge certificate and its stored token (used when switching to another choice)."""
    r = ctx.runner
    if not r.path_exists(CREDENTIALS_PATH):
        return False
    r.run(['certbot', 'delete', '--non-interactive', '--cert-name', CERT_NAME], sudo=True, check=False)
    r.remove_file(CREDENTIALS_PATH)
    r.run(['rm', '-f', APACHE_CERT, APACHE_KEY], sudo=True, check=False)
    return True


def plan(ctx, candidates=(), ddns_token=None):
    """Ask for the name, what the certificate covers, the contact email and the token.

    Returns (names, email, token, cert_names). `names` is this Pi's own name. `cert_names` is what the
    certificate is issued for: the same name, or a wildcard that includes it."""
    ui = ctx.ui
    explain(ui)
    default = None
    for c in candidates:
        try:
            if dnscheck.check_domain(ctx.http, lan_name(c)).status == 'cloudflare':
                default = c
                break
        except ValueError:
            continue

    zones = {}

    def on_cloudflare(v):
        h = lan_name(v)
        chk = dnscheck.check_domain(ctx.http, h)
        zones[h] = chk.zone if chk.status == 'cloudflare' else None
        if chk.status in ('other', 'missing'):
            raise ValueError(dnscheck.describe(chk) + ' The name must be in a domain whose DNS is hosted at Cloudflare.')
        return h
    name = ui.ask('LAN name for the certificate (e.g. rwsols.home.example.com)', default, validator=on_cloudflare)
    wildcard = ask_cover(ui, name, zones.get(name))
    ui.hint('Let\'s Encrypt emails this address only if a renewal is about to fail. It is not added to a mailing list.')

    def email(v):
        v = v.strip()
        if '@' not in v or '.' not in v.split('@')[-1] or ' ' in v:
            raise ValueError('That does not look like an email address.')
        return v
    address = ui.ask('Email address for renewal warnings', None, validator=email)
    return [name], address, get_token(ctx, name, ddns_token), [wildcard or name]
