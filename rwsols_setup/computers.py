"""The list of computers RWSOLS can wake, and the few settings that go with it, asked as questions and
written into config.php so nobody has to edit PHP by hand."""
import re

from . import phpconfig

SAMPLE_MAC = '00:00:00:00:00:00'
SLEEP_COMMANDS = ('suspend', 'hibernate', 'logoff', 'poweroff', 'forcepoweroff', 'lock', 'reboot')
SLEEP_UTILITY_URL = 'https://www.jeremyblum.com/wp-content/uploads/2013/07/SleepOnLan.zip'
DEFAULTS = {'MAX_PINGS': 15, 'SLEEP_TIME': 5, 'COMPUTER_SLEEP_CMD_PORT': 7760, 'COMPUTER_SLEEP_CMD': 'suspend'}

LIST_QUERY = 'What would you like to do with this list?'
LIST_OPTIONS = ['Keep the list as it is.', 'Add a computer.', 'Remove a computer.', 'Start the list over.']
ADD_ANOTHER = 'Add another computer?'
ADVANCED_QUERY = 'Change the sleep and timing settings? (The defaults suit most people.)'

HOW_TO_FIND = [
    'For each computer you need three things:',
    '    Name         anything you like; it is the label in the drop-down list on the RWSOLS page.',
    '    MAC address  the hardware address of the network adapter that is plugged into your network, '
    'for example 01:23:45:67:89:AB.',
    '    IP address   the computer\'s address on your home network, for example 192.168.1.20. RWSOLS pings it to see '
    'whether the computer is awake.',
    'On Windows, both are shown by running "ipconfig /all" in a Command Prompt: "Physical Address" is the MAC address and '
    '"IPv4 Address" is the IP address. Use the wired (Ethernet) adapter; Wake-on-LAN rarely works over Wi-Fi. '
    'On macOS and Linux, "ifconfig" or "ip addr" shows the same two values.',
    'Give each computer a fixed address in your router (often called a DHCP reservation), so the IP address you enter here '
    'stays correct.',
]


def computer_name(v):
    v = v.strip()
    if not v:
        raise ValueError('Enter a name.')
    if len(v) > 40 or not re.fullmatch(r'[A-Za-z0-9 ._()-]+', v):
        raise ValueError('Use up to 40 letters, digits, spaces, dots, dashes, underscores or brackets.')
    return v


def mac_address(v):
    """Accepts 01:23:45:67:89:ab, 01-23-45-67-89-AB and 0123.4567.89ab; returns 01:23:45:67:89:AB."""
    digits = re.sub(r'[:.\s-]', '', v.strip())
    if not re.fullmatch(r'[0-9A-Fa-f]{12}', digits):
        raise ValueError('A MAC address is 12 hexadecimal digits, for example 01:23:45:67:89:AB.')
    mac = ':'.join(digits[i:i + 2] for i in range(0, 12, 2)).upper()
    if mac in (SAMPLE_MAC, 'FF:FF:FF:FF:FF:FF'):
        raise ValueError('%s is a placeholder, not the address of a real network adapter.' % mac)
    return mac


def ipv4_address(v):
    v = v.strip()
    parts = v.split('.')
    if len(parts) != 4 or not all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts):
        raise ValueError('Enter an IPv4 address, for example 192.168.1.20.')
    return '.'.join(str(int(p)) for p in parts)


def _int_between(low, high):
    def check(v):
        if not (v.strip().isdigit() and low <= int(v) <= high):
            raise ValueError('Enter a number between %d and %d.' % (low, high))
        return int(v)
    return check


def read_computers(text):
    """[(name, mac, ip)] from config.php. The placeholder entries of the sample file do not count."""
    def items(key):
        try:
            v = phpconfig.get_value(text, key, [])
        except phpconfig.PhpConfigError:
            return []
        return [str(x) for x in v] if isinstance(v, list) else []
    rows = list(zip(items('COMPUTER_NAME'), items('COMPUTER_MAC'), items('COMPUTER_LOCAL_IP')))
    return [r for r in rows if r[1] != SAMPLE_MAC]


def _show(ui, computers):
    for i, (name, mac, ip) in enumerate(computers, 1):
        ui.say('  %d. %-20s %s   %s' % (i, name, mac, ip))


def _ask_one(ui, computers):
    taken = set(c[0].lower() for c in computers)

    def unique(v):
        v = computer_name(v)
        if v.lower() in taken:
            raise ValueError('There is already a computer called %s. Pick another name.' % v)
        return v
    number = len(computers) + 1
    name = ui.ask('Name of computer %d' % number, None, validator=unique)
    mac = ui.ask('MAC address of %s' % name, None, validator=mac_address)
    ip = ui.ask('IP address of %s on your home network' % name, None, validator=ipv4_address)
    return name, mac, ip


def _add_loop(ui, computers):
    while True:
        computers.append(_ask_one(ui, computers))
        ui.success('Added %s.' % computers[-1][0])
        if not ui.confirm(ADD_ANOTHER, False):
            return


def ask_computers(ui, existing):
    """Returns the new list of (name, mac, ip). Never returns an empty list."""
    computers = list(existing)
    ui.explain('The computers RWSOLS can wake', HOW_TO_FIND)
    if not computers:
        ui.say('Enter your computers one at a time. You are asked after each one whether there are more.')
        _add_loop(ui, computers)
    while True:
        ui.say('')
        ui.say('Computers in the list:')
        _show(ui, computers)
        i, _ = ui.choose(LIST_QUERY, LIST_OPTIONS, 1)
        if i == 1:
            return computers
        if i == 2:
            _add_loop(ui, computers)
        elif i == 3:
            if len(computers) == 1:
                ui.warn('The list needs at least one computer. Add the new one first, then remove this one.')
                continue
            n = ui.ask('Number of the computer to remove', None, validator=_int_between(1, len(computers)))
            ui.say('Removed %s.' % computers.pop(n - 1)[0])
        else:
            computers = []
            _add_loop(ui, computers)


def ask_advanced(ui, current):
    """Sleep command, sleep port and ping timing. Returns {} when the user leaves them alone."""
    ui.explain('Sleep and timing settings', [
        'Waking needs nothing installed on your computers. Putting a Windows computer to sleep from RWSOLS needs the small '
        'SleepOnLan utility running on it (%s); without it, the sleep button does nothing.' % SLEEP_UTILITY_URL,
        'Current settings: the sleep button sends "%s" to port %d. After waking or sleeping a computer, RWSOLS pings it up '
        'to %d times, %d seconds apart, to see whether it worked.'
        % (current['COMPUTER_SLEEP_CMD'], current['COMPUTER_SLEEP_CMD_PORT'], current['MAX_PINGS'], current['SLEEP_TIME'])])
    if not ui.confirm(ADVANCED_QUERY, False):
        return {}
    cmds = list(SLEEP_COMMANDS)
    dflt = cmds.index(current['COMPUTER_SLEEP_CMD']) + 1 if current['COMPUTER_SLEEP_CMD'] in cmds else 1
    _, cmd = ui.choose('What should the sleep button do to the computer?', cmds, dflt)
    return {
        'COMPUTER_SLEEP_CMD': cmd,
        'COMPUTER_SLEEP_CMD_PORT': ui.ask('Port the SleepOnLan utility listens on', str(current['COMPUTER_SLEEP_CMD_PORT']),
                                          validator=_int_between(1, 65535)),
        'MAX_PINGS': ui.ask('How many times to ping a computer before giving up', str(current['MAX_PINGS']),
                            validator=_int_between(1, 200)),
        'SLEEP_TIME': ui.ask('Seconds to wait between pings', str(current['SLEEP_TIME']), validator=_int_between(1, 60)),
    }


def current_settings(text):
    out = {}
    for key, dflt in DEFAULTS.items():
        try:
            v = phpconfig.get_value(text, key, dflt)
        except phpconfig.PhpConfigError:
            v = dflt
        out[key] = v if isinstance(v, type(dflt)) and not isinstance(v, bool) else dflt
    return out


def run(ui, config):
    """Ask everything and write it to config.php (a phpconfig.ConfigFile). Returns the list of computers."""
    text = config.read()
    computers = ask_computers(ui, read_computers(text))
    updates = {
        'COMPUTER_NAME': [c[0] for c in computers],
        'COMPUTER_MAC': [c[1] for c in computers],
        'COMPUTER_LOCAL_IP': [c[2] for c in computers],
    }
    updates.update(ask_advanced(ui, current_settings(text)))
    config.update(updates)
    ui.success('Saved %d computer%s to config.php.' % (len(computers), '' if len(computers) == 1 else 's'))
    return computers
