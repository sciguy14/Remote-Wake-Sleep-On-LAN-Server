"""Is the DNS for a domain managed by Cloudflare?

Cloudflare Tunnel and Cloudflare Access only work for names in a DNS zone that Cloudflare hosts. That is
visible from the outside: the zone's nameservers (NS records) end in ".ns.cloudflare.com". How the
domain's address is kept up to date (ddclient, a router, nothing at all) plays no part.

The lookup uses DNS over HTTPS against a public resolver, through the injectable HTTP transport, so it
needs no extra package and no API token. It never raises: a failed lookup is reported as 'unknown'."""
import json
from collections import namedtuple
from urllib.parse import urlencode

from .errors import SetupError

DOH_URL = 'https://cloudflare-dns.com/dns-query'
CLOUDFLARE_NS_SUFFIX = '.ns.cloudflare.com'
TYPE_NS, TYPE_SOA = 2, 6

# status: 'cloudflare' | 'other' | 'missing' (no such domain) | 'unknown' (lookup failed)
DnsCheck = namedtuple('DnsCheck', ['name', 'status', 'zone', 'nameservers'])


def _norm(name):
    return (name or '').strip().rstrip('.').lower()


def _query(http, name):
    res = http.request('GET', DOH_URL + '?' + urlencode({'name': name, 'type': 'NS'}),
                       headers={'Accept': 'application/dns-json'}, timeout=8)
    if res.status != 200:
        raise ValueError('HTTP %s' % res.status)
    data = json.loads(res.body.decode('utf8'))
    if not isinstance(data, dict) or 'Status' not in data:
        raise ValueError('not a DNS answer')
    return data


def _records(data, section, rtype):
    return [r for r in (data.get(section) or []) if isinstance(r, dict) and r.get('type') == rtype]


def check_domain(http, name):
    """Find the DNS zone that holds `name` and say who hosts it."""
    name = _norm(name)
    try:
        data = _query(http, name)
        ns = [_norm(r.get('data')) for r in _records(data, 'Answer', TYPE_NS)]
        zone = name
        if not ns:
            # Not a zone apex (or the name does not exist yet): the SOA record names the enclosing zone.
            soa = _records(data, 'Authority', TYPE_SOA)
            zone = _norm(soa[0].get('name')) if soa else ''
            if '.' not in zone or not (name == zone or name.endswith('.' + zone)):
                return DnsCheck(name, 'missing', None, [])
            ns = [_norm(r.get('data')) for r in _records(_query(http, zone), 'Answer', TYPE_NS)]
        ns = sorted(n for n in ns if n)
        if not ns:
            return DnsCheck(name, 'unknown', zone, [])
        status = 'cloudflare' if any(n.endswith(CLOUDFLARE_NS_SUFFIX) for n in ns) else 'other'
        return DnsCheck(name, status, zone, ns)
    except (SetupError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        return DnsCheck(name, 'unknown', None, [])


def describe(check):
    """One sentence for the user."""
    if check.status == 'cloudflare':
        return 'DNS for %s is managed by Cloudflare.' % check.zone
    if check.status == 'other':
        return 'DNS for %s is not managed by Cloudflare (its nameservers are %s).' % (check.zone, ', '.join(check.nameservers[:3]))
    if check.status == 'missing':
        return '%s does not appear to be a registered domain.' % check.name
    return 'Could not check who manages DNS for %s (the lookup failed).' % check.name
