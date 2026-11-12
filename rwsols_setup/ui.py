"""Wizard UI: one interface, two implementations.

RichUI is used when the optional `rich` package imports and stdin/stdout are a TTY. PlainUI (stdlib only)
is used otherwise (rich missing, piped input, tests). Both share all prompt logic in BaseUI; they only
differ in how text, boxes and tables are drawn and how a line is read.

Public interface (everything the package and setup.py use):
    heading(title)                      section title
    explain(title, lines, style)        short boxed explanation shown right before a question
    summary(title, lines, warnings)     closing panel
    say(text) / info(text) / hint(text) plain / accent / dim line
    warn(text) / error(text) / success(text)
    pause(text)                         "press Enter to continue"
    ask(prompt, default, validator)     re-asks until validator (returns the value or raises ValueError) accepts
    ask_secret(prompt, strip)           hidden entry
    confirm(prompt, default)            yes/no, Enter = default
    choose(query, options, default)     single select, returns (1-based index, option)
    choose_many(query, options, ...)    multi select with reasons, returns the selected values in display order
    checklist(title, rows)              rows of (label, ok, detail)
"""
import getpass
import importlib
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections import namedtuple

Option = namedtuple('Option', ['value', 'why', 'checked', 'locked'])
Option.__new__.__defaults__ = ('', False, False)

# One palette for both implementations: section headings, defaults, warnings, errors, success.
ANSI = {'heading': '1;36', 'accent': '0;36', 'prompt': '1', 'hint': '2', 'default': '0;35',
        'warn': '0;33', 'error': '0;31', 'success': '0;32'}
RICH = {'heading': 'bold cyan', 'accent': 'cyan', 'prompt': 'bold', 'hint': 'dim', 'default': 'magenta',
        'warn': 'yellow', 'error': 'bold red', 'success': 'green'}


def as_option(o):
    if isinstance(o, Option):
        return o
    if isinstance(o, (tuple, list)):
        return Option(*o)
    return Option(o)


def _items(lines, default_style=None):
    """Lines may be plain strings or (text, style) pairs."""
    out = []
    for ln in lines:
        out.append(tuple(ln) if isinstance(ln, (tuple, list)) else (ln, default_style))
    return out


class BaseUI(object):
    # ----- drawing and input primitives (implemented by PlainUI / RichUI) -----

    def _print(self, text='', style=None):
        raise NotImplementedError

    def _heading(self, title):
        raise NotImplementedError

    def _box(self, title, items, style):
        raise NotImplementedError

    def _table(self, headers, rows, styles=None):
        raise NotImplementedError

    def _checklist(self, title, rows):
        raise NotImplementedError

    def _read(self, segments):
        raise NotImplementedError

    def _read_secret(self, segments):
        raise NotImplementedError

    def _eof(self):
        self.error('Input ended unexpectedly. Aborting.')
        raise SystemExit(1)

    # ----- output -----

    def heading(self, title):
        self._heading(title)

    def explain(self, title, lines, style='accent'):
        self._box(title, _items(lines), style)

    def summary(self, title, lines, warnings=()):
        items = _items(lines) + _items(warnings, 'warn')
        self._box(title, items, 'success')

    def say(self, text=''):
        self._print(text)

    def info(self, text=''):
        self._print(text, 'accent')

    def hint(self, text=''):
        self._print(text, 'hint')

    def warn(self, text):
        self._print(text, 'warn')

    def error(self, text):
        self._print(text, 'error')

    def success(self, text):
        self._print(text, 'success')

    def checklist(self, title, rows):
        self._checklist(title, [(label, bool(ok), detail) for label, ok, detail in rows])

    # ----- input -----

    def pause(self, text='Press Enter to continue'):
        self._read([(text, 'prompt'), (' ', None)])

    def ask(self, prompt, default=None, validator=None):
        while True:
            segs = [(prompt, 'prompt')]
            if default:
                segs.append((' [%s]' % default, 'default'))
            segs.append((': ', None))
            v = self._read(segs).strip()
            if not v:
                v = default or ''
            if validator is None:
                return v
            try:
                return validator(v)
            except ValueError as e:
                self.warn(str(e))

    def ask_secret(self, prompt, strip=True):
        v = self._read_secret([(prompt, 'prompt'), (': ', None)])
        return v.strip() if strip else v

    def confirm(self, prompt, default=False):
        while True:
            v = self._read([(prompt, 'prompt'), (' [%s]' % ('Y/n' if default else 'y/N'), 'default'), (': ', None)]).strip().lower()
            if v == '':
                return default
            if v in ('y', 'yes'):
                return True
            if v in ('n', 'no'):
                return False
            self.warn('Please answer y or n.')

    def choose(self, query, options, default=None):
        """Returns (1-based index, option). Enter selects `default` (1-based) when given."""
        options = list(options)
        while True:
            self._print('')
            self._print(query, 'prompt')
            rows = [['%d)' % (i + 1), o + ('  (default)' if default == i + 1 else '')] for i, o in enumerate(options)]
            self._table(None, rows, ['default' if default == i + 1 else None for i in range(len(options))])
            segs = [('Choose a number (1-%d)' % len(options), 'prompt')]
            if default:
                segs.append((' [%d]' % default, 'default'))
            segs.append((': ', None))
            v = self._read(segs).strip()
            if v == '' and default:
                return default, options[default - 1]
            if v.isdigit() and 1 <= int(v) <= len(options):
                return int(v), options[int(v) - 1]
            self.warn('Please choose a number from the list!')

    def choose_many(self, query, options, validator=None, min_selected=1, add_prompt='Name to add'):
        """Multi select. `options` are Option(value, why, checked, locked). Locked entries are always selected.
        Type numbers (space or comma separated) to toggle, `a` to add your own entry, Enter to accept.
        Returns the selected values in display order."""
        opts = [as_option(o) for o in options]
        sel = [bool(o.locked or o.checked) for o in opts]
        while True:
            self._print('')
            self._print(query, 'prompt')
            rows = []
            for i, o in enumerate(opts):
                why = ('required: ' + o.why) if o.locked else o.why
                rows.append(['%d)' % (i + 1), '[x]' if sel[i] else '[ ]', o.value, why])
            self._table(['#', '', 'Name', 'Why'], rows)
            while True:
                raw = self._read([('Numbers to tick/untick, a to add your own, Enter to accept', 'hint'), (': ', None)]).strip()
                if raw == '':
                    if sum(sel) < min_selected:
                        self.warn('Select at least %d %s.' % (min_selected, 'entry' if min_selected == 1 else 'entries'))
                        continue
                    return [o.value for o, s in zip(opts, sel) if s]
                bad = []
                for tok in raw.replace(',', ' ').split():
                    if tok.lower() == 'a':
                        self._add_entry(opts, sel, validator, add_prompt)
                    elif tok.isdigit() and 1 <= int(tok) <= len(opts):
                        i = int(tok) - 1
                        if opts[i].locked:
                            self.warn('%s is required and cannot be unticked.' % opts[i].value)
                        else:
                            sel[i] = not sel[i]
                    else:
                        bad.append(tok)
                if bad:
                    self.warn('Not understood: %s. Type numbers from the list, or a.' % ', '.join(bad))
                break

    def _add_entry(self, opts, sel, validator, add_prompt):
        while True:
            v = self._read([(add_prompt, 'prompt'), (' (blank to cancel)', 'hint'), (': ', None)]).strip()
            if not v:
                return
            if validator is not None:
                try:
                    v = validator(v)
                except ValueError as e:
                    self.warn(str(e))
                    continue
            for i, o in enumerate(opts):
                if o.value == v:
                    sel[i] = True
                    self.info('%s is already in the list; it is now ticked.' % v)
                    return
            opts.append(Option(v, 'added by you', True, False))
            sel.append(True)
            return


def _term_width():
    return max(40, min(shutil.get_terminal_size((80, 24)).columns, 100))


def _wrap(text, width):
    if not text.strip():
        return ['']
    indent = text[:len(text) - len(text.lstrip())]
    return textwrap.wrap(text, width, initial_indent='', subsequent_indent=indent, break_long_words=False,
                         break_on_hyphens=False, drop_whitespace=True) or ['']


class PlainUI(BaseUI):
    """Stdlib only. Colors only on a color-capable TTY, so piped output stays clean."""

    def __init__(self, input_fn=None, secret_fn=None, out=None, color=None):
        self._input = input_fn or input
        self._secret = secret_fn or getpass.getpass
        self._out_stream = out
        if color is None:
            stream = self._out()
            color = bool(getattr(stream, 'isatty', lambda: False)()) and not os.environ.get('NO_COLOR') \
                and os.environ.get('TERM') != 'dumb'
        self.color = color

    def _out(self):
        return self._out_stream or sys.stdout

    def _c(self, text, style):
        if self.color and style in ANSI and text:
            return '\033[%sm%s\033[0m' % (ANSI[style], text)
        return text

    def _write(self, text):
        out = self._out()
        out.write(text)
        out.flush()

    def _print(self, text='', style=None):
        self._write(self._c(text, style) + '\n')

    def _heading(self, title):
        self._print('')
        self._print('=== %s ===' % title, 'heading')

    def _box(self, title, items, style):
        width = _term_width() - 4
        bar = self._c('|', style)
        self._print('')
        self._write('%s %s\n' % (bar, self._c(title, style if style != 'accent' else 'heading')))
        for text, st in items:
            for ln in _wrap(text, width):
                self._write('%s %s\n' % (bar, self._c(ln, st)) if ln else bar + '\n')

    def _table(self, headers, rows, styles=None):
        styles = styles or [None] * len(rows)
        ncol = len(headers) if headers else len(rows[0])
        widths = [0] * ncol
        for r in ([headers] if headers else []) + rows:
            for i in range(ncol - 1):
                widths[i] = max(widths[i], len(r[i]))
        pad = 2
        prefix = sum(w + pad for w in widths[:-1])
        room = max(20, _term_width() - 2 - prefix)
        if headers:
            self._write('  ' + ''.join(h.ljust(w + pad) for h, w in zip(headers[:-1], widths)) + self._c(headers[-1], 'hint') + '\n')
        for r, st in zip(rows, styles):
            head = ''.join(c.ljust(w + pad) for c, w in zip(r[:-1], widths))
            lines = _wrap(r[-1], room)
            for k, ln in enumerate(lines):
                self._write('  ' + (head if k == 0 else ' ' * len(head)) + self._c(ln, st) + '\n')

    def _checklist(self, title, rows):
        self._print('')
        self._print(title, 'prompt')
        for label, ok, detail in rows:
            if ok:
                self._print('  [ok]   ' + label, 'success')
            else:
                self._print('  [FAIL] %s%s' % (label, ' (%s)' % detail if detail else ''), 'error')

    def _read(self, segments):
        self._write(''.join(self._c(t, s) for t, s in segments))
        try:
            return self._input()
        except EOFError:
            self._eof()

    def _read_secret(self, segments):
        try:
            return self._secret(''.join(t for t, _ in segments))
        except EOFError:
            self._eof()


class RichUI(BaseUI):
    """Uses the optional `rich` package (python3-rich on Raspberry Pi OS)."""

    def __init__(self, console=None):
        from rich import box
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        self._box_style, self._Panel, self._Table, self._Text = box, Panel, Table, Text
        self.console = console or Console(highlight=False)

    def _t(self, text, style=None):
        return self._Text(text, style=RICH.get(style, '') or '')

    def _print(self, text='', style=None):
        self.console.print(self._t(text, style))

    def _heading(self, title):
        self.console.print()
        self.console.rule(self._t(title, 'heading'), style='cyan', align='left')

    def _box(self, title, items, style):
        body = self._Text()
        for i, (text, st) in enumerate(items):
            if i:
                body.append('\n')
            body.append(text, style=RICH.get(st, '') or '')
        kwargs = dict(title=self._t(' %s ' % title, 'heading' if style == 'accent' else style), border_style=RICH.get(style, ''),
                      box=self._box_style.ROUNDED, padding=(0, 1))
        try:
            panel = self._Panel(body, title_align='left', **kwargs)
        except TypeError:  # older rich without title_align
            panel = self._Panel(body, **kwargs)
        self.console.print(panel)

    def _table(self, headers, rows, styles=None):
        styles = styles or [None] * len(rows)
        table = self._Table(box=self._box_style.SIMPLE_HEAD if headers else None, show_header=bool(headers), show_edge=False,
                            pad_edge=False, header_style='dim')
        ncol = len(headers) if headers else len(rows[0])
        for i in range(ncol):
            table.add_column(headers[i] if headers else '', overflow='fold', no_wrap=(i < ncol - 1))
        for r, st in zip(rows, styles):
            table.add_row(*[self._t(c, st) for c in r])
        self.console.print(table)

    def _checklist(self, title, rows):
        self.console.print()
        self.console.print(self._t(title, 'prompt'))
        table = self._Table(box=None, show_header=False, pad_edge=False)
        table.add_column(no_wrap=True)
        table.add_column(overflow='fold')
        for label, ok, detail in rows:
            if ok:
                table.add_row(self._t('  ok', 'success'), self._t(label))
            else:
                table.add_row(self._t('  FAIL', 'error'), self._t(label + (' (%s)' % detail if detail else ''), 'error'))
        self.console.print(table)

    def _prompt(self, segments):
        return self._Text.assemble(*[(t, RICH.get(s, '')) if s else t for t, s in segments])

    def _read(self, segments):
        try:
            return self.console.input(self._prompt(segments))
        except EOFError:
            self._eof()

    def _read_secret(self, segments):
        try:
            return self.console.input(self._prompt(segments), password=True)
        except EOFError:
            self._eof()


# ----- selection and bootstrap -----

def _import_rich():
    for m in ('rich.box', 'rich.console', 'rich.panel', 'rich.table', 'rich.text'):
        importlib.import_module(m)


def _isatty(stream):
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def rich_available(importer=None):
    try:
        (importer or _import_rich)()
        return True
    except Exception:
        return False


def select_ui(stdin=None, stdout=None, importer=None):
    """RichUI when rich imports and stdin/stdout are TTYs; otherwise PlainUI."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if _isatty(stdin) and _isatty(stdout) and rich_available(importer):
        try:
            return RichUI()
        except Exception:
            pass
    return PlainUI()


def bootstrap_rich(skip=False, importer=None, run=None, which=shutil.which, say=print, stdin=None, stdout=None):
    """Try to install python3-rich with apt (never pip). Returns True only if an install was attempted.
    Silent on any failure: the wizard simply continues with PlainUI. Skipped for --help/--dry-run (`skip`),
    when rich is already importable, when not on an interactive terminal, and when apt is not available."""
    try:
        if skip or rich_available(importer):
            return False
        stdin = sys.stdin if stdin is None else stdin
        stdout = sys.stdout if stdout is None else stdout
        if not (_isatty(stdin) and _isatty(stdout)):
            return False
        if not which('apt-cache') or not which('apt-get'):
            return False
        run = run or subprocess.run
        pol = run(['apt-cache', 'policy', 'python3-rich'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        m = re.search(r'Candidate:\s*(\S+)', pol.stdout or '')
        if pol.returncode != 0 or not m or m.group(1) == '(none)':
            return False
        say('Installing python3-rich for a nicer setup experience...')
        run(['sudo', 'apt-get', 'install', '-y', 'python3-rich'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        importlib.invalidate_caches()
        return True
    except Exception:
        return False
