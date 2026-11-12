"""Host name validation and the reference implementation of the contract's Host matching."""
import ipaddress
import re

_LABEL = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)$')
_PORT = re.compile(r'^:[0-9]+$')
LOCALHOST_ENTRIES = ('localhost', '127.0.0.1', '[::1]')


def validate_hostname(value):
    """Return the normalized (lowercase, no trailing dot) hostname / IP literal, or raise ValueError.
    IPv6 literals are returned bracketed. Only values passing this may be written to config.php or Apache."""
    if not isinstance(value, str):
        raise ValueError('not a string')
    h = value.strip().lower()
    if h.endswith('.'):
        h = h[:-1]
    if not h or len(h) > 253:
        raise ValueError('invalid hostname %r' % value)
    if ':' in h:
        inner = h[1:-1] if h.startswith('[') and h.endswith(']') else h
        try:
            ipaddress.IPv6Address(inner)
        except ValueError:
            raise ValueError('invalid IPv6 address %r' % value)
        return '[' + inner + ']'
    labels = h.split('.')
    if all(re.fullmatch(r'[0-9]+', x) for x in labels):
        try:
            ipaddress.IPv4Address(h)
        except ValueError:
            raise ValueError('invalid IPv4 address %r' % value)
        if not all(x == str(int(x)) for x in labels):
            raise ValueError('invalid IPv4 address %r' % value)
        return h
    if not all(_LABEL.match(x) for x in labels):
        raise ValueError('invalid hostname %r' % value)
    return h


def normalize_host(raw):
    """Contract normalization of a request Host header. None means missing/malformed (always rejected)."""
    if not raw:
        return None
    h = raw.lower()
    if h.startswith('['):
        end = h.find(']')
        if end < 0:
            return None
        rest = h[end + 1:]
        if rest and not _PORT.match(rest):
            return None
        return h[:end + 1]
    if ':' in h:
        host, _, port = h.rpartition(':')
        if not _PORT.match(':' + port):
            return None
        h = host
    if ':' in h:
        return None
    if h.endswith('.'):
        h = h[:-1]
    return h or None


def is_allowed(raw_host, allowed, allow_localhost=False):
    entries = set(a.lower().rstrip('.') for a in allowed)
    if allow_localhost:
        entries.update(LOCALHOST_ENTRIES)
    if not entries:
        return True
    n = normalize_host(raw_host)
    return n is not None and n in entries
