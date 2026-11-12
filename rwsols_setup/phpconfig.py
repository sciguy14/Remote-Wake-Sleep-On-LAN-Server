"""Read and update simple top-level `$NAME = value;` assignments in config.php, in place."""
import re

from .errors import SetupError

BLOCK_HEADER = '// ---- Remote access settings (managed by setup.py; safe to edit by hand) ----'
MISSING = object()


class PhpConfigError(SetupError):
    pass


def render_value(v):
    if v is None:
        return 'NULL'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        return 'array(' + ', '.join(render_value(x) for x in v) + ')'
    if isinstance(v, str):
        if any(ord(c) < 0x20 for c in v):
            raise PhpConfigError('Refusing to write a control character into config.php')
        return "'" + v.replace('\\', '\\\\').replace("'", "\\'") + "'"
    raise PhpConfigError('Unsupported value type %r' % type(v))


def _find_statement(text, name):
    """Return (start, end, value_text, indent) of the top-level assignment, or None."""
    m = re.search(r'(?m)^([ \t]*)\$' + re.escape(name) + r'[ \t]*=', text)
    if not m:
        return None
    i = m.end()
    quote = None
    while i < len(text):
        c = text[i]
        if quote:
            if c == '\\':
                i += 1
            elif c == quote:
                quote = None
        elif c in '\'"':
            quote = c
        elif c == ';':
            return m.start(), i + 1, text[m.end():i], m.group(1)
        i += 1
    raise PhpConfigError('Unterminated assignment of $%s in config.php' % name)


def _split_items(s):
    items, cur, quote, i = [], '', None, 0
    while i < len(s):
        c = s[i]
        if quote:
            cur += c
            if c == '\\' and i + 1 < len(s):
                i += 1
                cur += s[i]
            elif c == quote:
                quote = None
        elif c in '\'"':
            quote = c
            cur += c
        elif c == ',':
            items.append(cur)
            cur = ''
        else:
            cur += c
        i += 1
    if cur.strip():
        items.append(cur)
    return items


def parse_value(s):
    s = s.strip()
    low = s.lower()
    if low == 'null':
        return None
    if low in ('true', 'false'):
        return low == 'true'
    if re.fullmatch(r'-?[0-9]+', s):
        return int(s)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in '\'"':
        body = s[1:-1]
        if s[0] == "'":
            return re.sub(r"\\([\\'])", r'\1', body)
        return re.sub(r'\\([\\"$])', r'\1', body)
    m = re.fullmatch(r'(?is)array\s*\((.*)\)|\[(.*)\]', s)
    if m:
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        return [parse_value(x) for x in _split_items(inner)]
    raise PhpConfigError('Cannot parse PHP value: %s' % s)


def get_value(text, name, default=MISSING):
    st = _find_statement(text, name)
    if st is None:
        if default is MISSING:
            raise KeyError(name)
        return default
    return parse_value(st[2])


def update_text(text, updates):
    """Return text with `updates` ({name: python value}) applied. Existing assignments are replaced in
    place (everything else is byte-identical); missing ones are appended in a marked block."""
    missing = []
    for name, value in updates.items():
        rendered = '$%s = %s;' % (name, render_value(value))
        st = _find_statement(text, name)
        if st is None:
            missing.append(rendered)
        else:
            start, end, _, indent = st
            text = text[:start] + indent + rendered + text[end:]
    if missing:
        nl = '\r\n' if '\r\n' in text else '\n'
        block = nl.join(([] if BLOCK_HEADER in text else [BLOCK_HEADER]) + missing) + nl
        idx = text.rfind('?>')
        if idx >= 0:
            text = text[:idx] + block + nl + text[idx:]
        else:
            text = text + ('' if text.endswith('\n') else nl) + nl + block
    return text


class ConfigFile(object):
    def __init__(self, path, runner=None, dry_run=False):
        self.path = path
        self.runner = runner
        self.dry_run = dry_run

    def read(self):
        with open(self.path, 'r', encoding='utf8', newline='') as f:
            return f.read()

    def exists(self):
        import os
        return os.path.exists(self.path)

    def get(self, name, default=None):
        return get_value(self.read(), name, default)

    def update(self, updates):
        old = self.read()
        new = update_text(old, updates)
        if new == old:
            return False
        if self.dry_run:
            return True
        try:
            with open(self.path, 'w', encoding='utf8', newline='') as f:
                f.write(new)
        except PermissionError:
            if self.runner is None:
                raise
            self.runner.run(['tee', self.path], input=new, sudo=True)
        return True
