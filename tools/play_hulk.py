"""Start one offline practice session; close the game to stop its server."""
import argparse
import base64
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

LAB = Path(__file__).resolve().parents[1]
SBPE = Path(os.environ.get('HULK_SBPE', str(LAB.parent)))
GAME = Path(os.environ.get('HULK_GAME', str(LAB.parent / 'StarBreak')))

if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

from localserver import game_settings

OFFLINE_DISABLED_PLUGINS=('combat_recorder','design_recorder','map_export',
    'sb_hardlab_probe','hardlab_delta','hardlab_server','hardlab_spawn')

RUNTIME_PLUGIN_FILES = ('hardlab_live.py', '_hardlab_livebridge.py', '_practice_keybindings.py', '_full_run_inventory.py',
                        '_native_replay_hooks.py', '_native_replay.py', '_native_replay_capture.py', '_native_replay_player.py', '_native_battle_metrics.py',
                        '_full_run_ground.py', '_full_run_egress.py', '_full_run_egress_transport.py',
                        '_full_run_ground_restore.py', '_full_run_ground_restore_transport.py',
                        '_dumb_boss_teleports.py','_dumb_wave_teleports.py',
                        '_hardlab_delta.py','runtime_pb_layouts.json')

def _protobuf_varint(data, offset):
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 63:
            break
    raise ValueError('invalid protobuf varint')

def _protobuf_fields(data):
    """Yield (field_number, wire_type, value) for a small protobuf blob."""
    offset = 0
    while offset < len(data):
        tag, offset = _protobuf_varint(data, offset)
        field = tag >> 3
        wire = tag & 7
        if wire == 0:
            value, offset = _protobuf_varint(data, offset)
        elif wire == 1:
            value = data[offset:offset + 8]
            offset += 8
        elif wire == 2:
            length, offset = _protobuf_varint(data, offset)
            value = data[offset:offset + length]
            offset += length
        elif wire == 5:
            value = data[offset:offset + 4]
            offset += 4
        else:
            raise ValueError('unsupported protobuf wire type')
        yield field, wire, value

def _keycode_to_scancode(keycode):
    """Convert StarBreak's stored SDL keycode to an SDL scancode."""
    if keycode is None:
        return None
    keycode = int(keycode)
    if keycode <= 0:
        return None
    if keycode & 0x40000000:
        scancode = keycode & 0x3fffffff
        return scancode if scancode > 0 else None
    if 65 <= keycode <= 90:
        keycode += 32
    if 97 <= keycode <= 122:
        return 4 + keycode - 97
    digit_scancodes = {
        48: 39, 49: 30, 50: 31, 51: 32, 52: 33,
        53: 34, 54: 35, 55: 36, 56: 37, 57: 38,
    }
    if keycode in digit_scancodes:
        return digit_scancodes[keycode]
    punctuation = {
        8: 42, 9: 43, 13: 40, 27: 41, 32: 44,
        39: 52, 44: 54, 45: 45, 46: 55, 47: 56,
        59: 51, 61: 46, 91: 47, 92: 49, 93: 48,
        96: 53,
    }
    if keycode in punctuation:
        return punctuation[keycode]
    if 0 < keycode < 512:
        return keycode
    return None

def detect_interact_scancode(game_dir=None):
    """Read the user's actual interaction key from StarBreak LocalStorage."""
    override = os.environ.get('HULK_INTERACT_SCANCODE', '').strip()
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    candidates = []
    appdata = os.environ.get('APPDATA', '').strip()
    if appdata:
        candidates.append(Path(appdata) / 'Crunchy Games' / 'StarBreak' /
                         'LocalStorage.pb')
    if game_dir:
        candidates.append(Path(game_dir) / 'LocalStorage.pb')
    for path in candidates:
        try:
            encoded = path.read_bytes().strip()
            storage = base64.b64decode(encoded)
            mapping = None
            for field, wire, entry in _protobuf_fields(storage):
                if field != 1 or wire != 2:
                    continue
                key = None
                value = None
                for efield, ewire, item in _protobuf_fields(entry):
                    if efield == 1 and ewire == 2:
                        key = item.decode('utf-8', 'replace')
                    elif efield == 2 and ewire == 2:
                        value = item
                if key == 'impbstr':
                    mapping = value
                    break
            if mapping is None:
                continue
            for field, wire, item in _protobuf_fields(mapping):
                if field != 1 or wire != 2:
                    continue
                cinput = None
                keys = []
                for kfield, kwire, keyvalue in _protobuf_fields(item):
                    if kwire != 0:
                        continue
                    if kfield == 1:
                        cinput = keyvalue
                    elif kfield in (2, 3, 4):
                        keys.append(keyvalue)
                if cinput != 7:
                    continue
                for keyvalue in keys:
                    scancode = _keycode_to_scancode(keyvalue)
                    if scancode is not None:
                        return scancode
        except (IOError, OSError, ValueError, TypeError):
            continue
    return 15

def game_from_sbpe_config(sbpe):
    config = Path(sbpe) / 'config.ini'
    if not config.is_file():
        return None
    parser = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    with open(str(config), 'rb') as handle:
        data = handle.read()
    for encoding in ('utf-8', 'gb18030', 'mbcs'):
        try:
            parser.read_string(data.decode(encoding))
            break
        except (UnicodeDecodeError, UnicodeError, configparser.Error):
            parser = configparser.ConfigParser(
                inline_comment_prefixes=('#', ';'))
    else:
        raise RuntimeError('Cannot decode SBPE config.ini')
    raw = parser.get('general', 'game', fallback='').strip().strip('"')
    if not raw:
        return None
    configured = Path(raw)
    local = configured.parent / 'mvmmoclient_local2.exe'
    return local if local.is_file() else configured

def prepare_local_client(game_dir):
    from make_local_client import main as make_local_main
    from make_local_client import parse_pe_sections, va_to_offset
    from patch_client_bounds import GUARDS, jump_patch, main as patch_bounds_main
    make_local_main(['--game-dir', str(game_dir)])
    target = Path(game_dir) / 'mvmmoclient_local2.exe'
    if target.is_file():
        try:
            binary = target.read_bytes()
            image_base, sections = parse_pe_sections(binary)
            if all(binary[va_to_offset(sections, image_base, va):
                        va_to_offset(sections, image_base, va) + 6]
                       == jump_patch(va) for va in GUARDS):
                return
        except (IOError, OSError, ValueError, IndexError):
            pass
    result = patch_bounds_main(['--game-dir', str(game_dir)])
    if result:
        raise SystemExit('Could not prepare mvmmoclient_local2.exe')

def copy_tree(source, target):
    """Copy a small SBPE tree using APIs available in embedded PyPy 3.5."""
    source = str(source)
    target = str(target)
    for current, directories, files in os.walk(source):
        directories[:] = [name for name in directories if name != '__pycache__']
        relative = os.path.relpath(current, source)
        destination = target if relative == '.' else os.path.join(target, relative)
        if not os.path.isdir(destination):
            os.makedirs(destination)
        for name in files:
            if name.endswith('.pyc') or '.bak' in name:
                continue
            shutil.copy2(os.path.join(current, name),
                         os.path.join(destination, name))

def _python_kind(path):
    """Return (implementation, version) for a usable interpreter, else None."""
    if not os.path.isfile(str(path)):
        return None
    script = ('import sys\n'
              'if sys.version_info < (3, 10): sys.exit(1)\n'
              'print(sys.implementation.name + " " + sys.version.split()[0])\n')
    try:
        result = subprocess.run([str(path), '-c', script],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except (IOError, OSError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.decode('ascii', 'replace').strip().split()
    if len(parts) != 2:
        return None
    return parts[0], parts[1]

def bundled_tk_environment():
    """Locate the player package's Tcl/Tk libraries for embedded Python."""
    if os.environ.get('TCL_LIBRARY') and os.environ.get('TK_LIBRARY'):
        return None
    for base in (LAB / 'python', LAB.parent / 'python'):
        tcl = base / 'tcl' / 'tcl8.6'
        tk = base / 'tcl' / 'tk8.6'
        if tcl.is_dir() and tk.is_dir():
            os.environ.setdefault('TCL_LIBRARY', str(tcl))
            os.environ.setdefault('TK_LIBRARY', str(tk))
            return base
    return None

def choose_editor_python():
    """Return the CPython 3.12 interpreter used by the shared desktop host.

    Prefers the interpreter running this launcher (in the package that is
    the bundled python/, which ships Tk), then any installed CPython.  The
    The embedded viewer has a pinned ABI; never choose the game's 32-bit
    PyPy interpreter or a different Python version just because Tk is present.
    """
    bundled_tk_environment()
    candidates = [sys.executable]
    for name in ('python', 'python3', 'py'):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    seen = set()
    for candidate in candidates:
        try:
            key = os.path.normcase(os.path.abspath(candidate))
        except (TypeError, ValueError):
            continue
        if key in seen or not os.path.isfile(candidate):
            continue
        seen.add(key)
        try:
            result = subprocess.run(
                [str(candidate), '-c', 'import sys,platform; assert platform.python_implementation()=="CPython" and sys.version_info[:2]==(3,12) and sys.maxsize>2**32'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (IOError, OSError):
            continue
        if result.returncode == 0:
            return str(candidate)
    return None

def choose_server_python(explicit=None):
    """Prefer bundled CPython 3.10+ for the 60 Hz server loop.

    SBPE's old 32-bit PyPy is reserved for injection and cannot host this server.
    """
    forced = explicit or os.environ.get('HULK_SERVER_PYTHON')
    if forced:
        kind = _python_kind(forced)
        if kind is None:
            raise SystemExit('Cannot run the server with Python 3.10+: ' + str(forced))
        return str(forced), kind[0], kind[1]
    candidates = []
    for bundled in (LAB / 'python' / 'python.exe',
                    LAB.parent / 'python' / 'python.exe'):
        if bundled.is_file():
            candidates.append(str(bundled))
    if getattr(sys.implementation, 'name', '') == 'cpython':
        candidates.append(sys.executable)
    for name in ('python', 'python3'):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.append(sys.executable)
    seen = set()
    fallback = None
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        kind = _python_kind(candidate)
        if kind is None:
            continue
        if fallback is None:
            fallback = (str(candidate), kind[0], kind[1])
        if kind[0] == 'cpython':
            return str(candidate), kind[0], kind[1]
    if fallback is not None:
        return fallback
    raise SystemExit('No Python 3.10+ runtime found. Use the Windows player ZIP.')

def default_runtime_dir(sbpe, profile):
    """Return an isolated runtime outside the installed SBPE directory."""
    root = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    identity = os.path.normcase(os.path.abspath(str(sbpe))).encode('utf-8')
    token = hashlib.md5(identity).hexdigest()[:12]
    return Path(root) / 'HulkHardSimulator' / token / profile

BOM_UTF8 = b'\xef\xbb\xbf'
SECTION = re.compile(r'^\s*\[([^\]]+)\]')

def decode_config(data):
    """Decode config.ini the way it was written so we can put it back."""
    if data.startswith(BOM_UTF8):
        return data.decode('utf-8-sig'), 'utf-8-sig'
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return data.decode('gb18030'), 'gb18030'

def _find_section(lines, section):
    """Return (first, last) line indexes of [section], or None."""
    start = None
    for index, line in enumerate(lines):
        match = SECTION.match(line)
        if match is None:
            continue
        if start is not None:
            return start, index
        if match.group(1).strip().lower() == section.lower():
            start = index
    if start is not None:
        return start, len(lines)
    return None

def _is_option(line, key):
    head = line.split('=', 1)[0].strip().lower()
    return '=' in line and head == key.lower()

def ini_has(text, section, key):
    """True when [section] already carries ``key``."""
    lines = text.splitlines(True)
    found = _find_section(lines, section)
    if found is None:
        return False
    start, end = found
    return any(_is_option(lines[index], key) for index in range(start + 1, end))

def ini_set(text, section, key, value):
    """Set one INI option, leaving every other byte of the file alone."""
    lines = text.splitlines(True)
    replacement = '{0} = {1}\n'.format(key, value)
    found = _find_section(lines, section)
    if found is None:
        if lines and not lines[-1].endswith('\n'):
            lines.append('\n')
        lines.append('[{0}]\n'.format(section))
        lines.append(replacement)
        return ''.join(lines)
    start, end = found
    for index in range(start + 1, end):
        if _is_option(lines[index], key):
            lines[index] = replacement
            return ''.join(lines)
    insert = end
    while insert - 1 > start and not lines[insert - 1].strip():
        insert -= 1
    if insert > 0 and not lines[insert - 1].endswith('\n'):
        lines.insert(insert, '\n')
        insert += 1
    lines.insert(insert, replacement)
    return ''.join(lines)

class InstalledSbpeRuntime(object):
    """Run inside the installed SBPE folder: its plugins, its config.ini.

    ``remote.py`` resolves everything from the folder that holds the symbol
    file, so pointing the symbol file at the install makes the game load every
    plugin in ``<sbpe>/plugins`` with ``<sbpe>/config.ini`` -- exactly like the
    ordinary SBPE launcher.  Only the bridge plugin's own options are switched
    on for the session; the previous config bytes are restored afterwards.
    """

    def __init__(self, sbpe, bridge_port, game_dir=None, diagnostics=False,
                 show_green_squids=False):
        self.root = Path(os.path.abspath(str(sbpe)))
        self.config_path = self.root / 'config.ini'
        self.bridge_port = int(bridge_port)
        self.game_dir = game_dir
        self.diagnostics = diagnostics
        self.show_green_squids = bool(show_green_squids)
        self.original = None
        self.patched = None
        self.backup = None
        self.copied = []
        self.wrote_config = False

    def check(self):
        needed = ['symbols.json', 'util.py', 'manager.py', 'bpb_pb2.py',
                  'config.ini', 'cdefs/proto.json', 'plugins']
        missing = [str(self.root / name) for name in needed
                   if not (self.root / name).exists()]
        if missing:
            raise SystemExit('Not an SBPE install (missing):\n'
                             + '\n'.join(missing))
        return self.root

    def prepare(self, write=True):
        """Enable the bridge for this run; return the SBPE root as runtime."""
        self.check()
        for name in RUNTIME_PLUGIN_FILES:
            source = LAB / 'plugins' / name
            target = self.root / 'plugins' / name
            if not source.is_file():

                print('Warning: missing runtime plugin: ' + str(source))
                continue
            stale = (not target.is_file()
                     or target.read_bytes() != source.read_bytes())
            if stale:
                self.copied.append(name)
                if write:
                    shutil.copy2(str(source), str(target))
        self.original = self.config_path.read_bytes()
        text = decode_config(self.original)[0]
        text = ini_set(text, 'plugin_hardlab_live', 'enabled', 'yes')
        text = ini_set(text, 'plugin_hardlab_live', 'host', '127.0.0.1')
        text = ini_set(text, 'plugin_hardlab_live', 'port', str(self.bridge_port))
        text = ini_set(text, 'plugin_hardlab_live', 'interact_scancode', '0')
        text = ini_set(text, 'plugin_hardlab_live', 'diagnostics_file',str(self.root/'native-state.jsonl') if self.diagnostics else '')

        squid_plugin = self.root / 'plugins' / 'squid_prediction3.py'
        self.squid_option_patched = False
        if self.show_green_squids and squid_plugin.is_file():
            text = ini_set(text, 'plugin_squid_prediction3',
                           'hide_green_squids', 'no')
            self.squid_option_patched = True

        codec = decode_config(self.original)[1]
        self.patched = text.encode(codec)
        if self.patched != self.original and write:
            self.backup = self.config_path.with_name(
                self.config_path.name + '.bak-hulk-'
                + time.strftime('%Y%m%d-%H%M%S'))
            shutil.copy2(str(self.config_path), str(self.backup))
            with self.config_path.open('wb') as handle:
                handle.write(self.patched)
            self.wrote_config = True
        return self.root

    def finish(self):
        """Restore an opted-in squid-prediction toggle, report the rest."""
        if self.squid_option_patched and self.wrote_config:
            with self.config_path.open('wb') as handle:
                handle.write(self.original)
            print('config.ini restored after --show-green-squids in '
                  + str(self.config_path), flush=True)
        elif self.wrote_config:
            print('The [plugin_hardlab_live] section stays in the SBPE '
                  'config; ordinary SBPE sessions load the plugin in its '
                  'dormant state. Backup: ' + str(self.backup), flush=True)

def prepare_runtime(sbpe, diagnostics=False, runtime=None, bridge_port=21550,
                    user_plugins=False, game_dir=None):

    sbpe=Path(os.path.abspath(str(sbpe)))
    profile='sbpe-user' if user_plugins else 'sbpe'
    runtime = Path(os.path.abspath(str(
        runtime if runtime else default_runtime_dir(sbpe, profile))))
    if runtime==sbpe or sbpe in runtime.parents:
        raise ValueError('Offline runtime must be outside the installed SBPE folder')
    (runtime / 'plugins').mkdir(parents=True, exist_ok=True)
    for name in ('symbols.json', 'util.py', 'manager.py', 'bpb_pb2.py'):
        shutil.copy2(str(sbpe / name), str(runtime / name))
    copy_tree(sbpe / 'cdefs', runtime / 'cdefs')
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform=str
    if user_plugins:

        copy_tree(sbpe / 'plugins', runtime / 'plugins')
        config_path=runtime/'config.ini'
        if config_path.exists():
            config.read(config_path,encoding='utf8')
            source_config=configparser.ConfigParser(interpolation=None)
            source_config.optionxform=str
            source_config.read(str(sbpe / 'config.ini'), encoding='utf-8-sig')

            for section in source_config.sections():
                if not config.has_section(section):continue
                for key in source_config[section]:
                    lower=key.lower()
                    if lower!=key and lower in config[section] and key not in config[section]:
                        config[section][key]=config[section].pop(lower)
        else:
            config.read(str(sbpe / 'config.ini'), encoding='utf-8-sig')
            for name in ('more_misc','noise'):
                section='plugin_'+name
                if not config.has_section(section):config.add_section(section)

            config['plugin_more_misc']['draw_bg']='no'
            config['plugin_more_misc']['hide_extras']='no'
            config['plugin_noise']['hide_effects']='no'
        for name in OFFLINE_DISABLED_PLUGINS:
            section='plugin_'+name
            if not config.has_section(section):config.add_section(section)
            config[section]['enabled']='no'
        if not config.has_section('manager'):config.add_section('manager')
        priority=config.get('manager','priority',fallback=config.get('general','priority',fallback=''))
        priority=['plugin_hardlab_live']+[p if p.startswith('plugin_') else 'plugin_'+p
            for p in priority.split() if p not in ('hardlab_live','plugin_hardlab_live')]
        config['manager']['priority']=' '.join(priority)
        (runtime/'plugin-profile.json').write_text(json.dumps(dict(
            source=str(sbpe),disabled=list(OFFLINE_DISABLED_PLUGINS),
            modules=sorted(p.stem for p in (runtime/'plugins').glob('*.py')
                           if not p.name.startswith('_'))),indent=2),encoding='utf8')
    for name in RUNTIME_PLUGIN_FILES:
        source = LAB / 'plugins' / name
        if not source.is_file():
            print('Warning: missing runtime plugin: ' + str(source))
            continue
        shutil.copy2(str(source), str(runtime / 'plugins' / name))
    if not config.has_section('general'):config.add_section('general')
    config['general']['mipmaps']='no'
    if not config.has_section('plugin_hardlab_live'):config.add_section('plugin_hardlab_live')
    config['plugin_hardlab_live']['enabled']='yes'
    config['plugin_hardlab_live']['host']='127.0.0.1'
    config['plugin_hardlab_live'].setdefault('hud_visible','yes')
    config['plugin_hardlab_live']['port'] = str(bridge_port)
    config['plugin_hardlab_live']['interact_scancode'] = '0'
    if diagnostics:
        config['plugin_hardlab_live']['diagnostics_file'] = str(runtime / 'native-state.jsonl')
    else:
        config['plugin_hardlab_live'].pop('diagnostics_file',None)
    with (runtime / 'config.ini').open('w',encoding='utf8') as handle:
        config.write(handle)
    return runtime

def resolve_map(requested=None):
    """Return (map_id, source): --map flag, game_settings.json or default.

    The available list is the maps/ scan, so a map pack dropped into that
    folder is playable without touching this launcher.
    """
    available = game_settings.available_maps()
    if requested:
        if requested not in available:
            raise SystemExit('Unknown map {0!r}; available: {1}'.format(
                requested, ', '.join(available)))
        return requested, 'command line'
    configured = game_settings.load().get('map')
    if configured in available:
        return configured, 'game_settings.json'
    return game_settings.ORIGINAL_MAP, 'default'

def port_bind_error(port, kind=socket.SOCK_STREAM):
    """Return the OSError that stops 127.0.0.1:<port> binding, else None."""
    try:
        with socket.socket(socket.AF_INET, kind) as sock:
            sock.bind(('127.0.0.1', port))
    except OSError as error:
        return error
    return None

PORT_FALLBACKS = (3148, 4148, 5148, 12048, 22048)

RECLAIM_MIN_AGE_SECONDS = 60.0

def _netstat_rows(kind=socket.SOCK_STREAM):
    """``netstat -ano`` rows as (local, remote, state, pid) tuples."""
    proto = 'TCP' if kind == socket.SOCK_STREAM else 'UDP'
    try:
        output = subprocess.check_output(
            ['netstat', '-ano', '-p', proto], text=True, errors='replace',
            timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].lower().startswith(proto.lower()):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if proto == 'TCP':
            if len(parts) < 5:
                continue
            rows.append((parts[1], parts[2], parts[3].upper(), pid))
        else:
            rows.append((parts[1], parts[2], '', pid))
    return rows

def port_owner_pid(port, kind=socket.SOCK_STREAM):
    """PID bound to 127.0.0.1:<port> (or a wildcard address), else None."""
    suffix = ':{0}'.format(int(port))
    for local, _remote, state, pid in _netstat_rows(kind):
        if not local.endswith(suffix):
            continue
        host = local.rsplit(':', 1)[0].strip('[]')
        if host not in ('127.0.0.1', '0.0.0.0', '::1', '::'):
            continue
        if kind == socket.SOCK_STREAM and state not in ('LISTENING', ''):
            continue
        return pid
    return None

def port_has_client(port):
    """True when a TCP client is connected to 127.0.0.1:<port>."""
    suffix = ':{0}'.format(int(port))
    for local, remote, state, _pid in _netstat_rows(socket.SOCK_STREAM):
        if state == 'ESTABLISHED' and (local.endswith(suffix)
                                       or remote.endswith(suffix)):
            return True
    return False

def _process_details(pid):
    """(command line, creation time string) for *pid*, or ('', None)."""
    script = ("$p = Get-CimInstance Win32_Process -Filter 'ProcessId={0}';"
              " if ($p) {{ $p | Select-Object CommandLine,CreationDate |"
              " ConvertTo-Json -Compress }}").format(int(pid))
    commands = (
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
        ['wmic', 'process', 'where', 'processid={0}'.format(int(pid)),
         'get', 'commandline,creationdate', '/format:list'],
    )
    for command in commands:
        try:
            output = subprocess.check_output(command, text=True,
                                             errors='replace', timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        output = output.strip()
        if not output:
            continue
        if output.startswith('{'):
            try:
                row = json.loads(output)
            except ValueError:
                row = {}
            return (row.get('CommandLine') or '', row.get('CreationDate'))
        command_line, creation = '', None
        for line in output.splitlines():
            key, _, value = line.partition('=')
            key = key.strip().lower()
            if key == 'commandline':
                command_line = value.strip()
            elif key == 'creationdate':
                creation = value.strip()
        if command_line:
            return command_line, creation
    return '', None

def _process_age_seconds(creation):
    """Seconds since a CIM/wmic ``CreationDate`` string, or None."""
    text = str(creation or '').strip()
    if not text:
        return None
    stamp = re.search(r'/Date\((\d+)\)/', text)
    if stamp:
        return max(0.0, time.time() - float(stamp.group(1)) / 1000.0)
    digits = re.sub(r'[^0-9]', '', text.split('.')[0])
    if len(digits) < 14:
        return None
    try:
        created = time.mktime(time.strptime(digits[:14], '%Y%m%d%H%M%S'))
    except ValueError:
        return None
    return max(0.0, time.time() - created)

def _looks_like_simulator_server(command_line):
    """True for ``python -m localserver.server --live`` style processes."""
    text = str(command_line or '').lower()
    if 'localserver.server' not in text:
        return False
    if 'python' not in text and 'pypy' not in text:
        return False
    return '--live' in text or '--mode hulk' in text

def stale_server_owner(port, kind=socket.SOCK_STREAM):
    """PID of an abandoned simulator server holding *port*, else None.

    A live session (client connected) or a server that just started is not
    stale and is never touched.  Read-only, so ``--check`` can report it.
    """
    owner = port_owner_pid(port, kind)
    if not owner or owner == os.getpid():
        return None
    command_line, creation = _process_details(owner)
    if not _looks_like_simulator_server(command_line):
        return None
    age = _process_age_seconds(creation)
    if age is None or age < RECLAIM_MIN_AGE_SECONDS:
        return None
    if kind == socket.SOCK_STREAM:
        if port_has_client(port):
            return None
    else:
        match = re.search(r'--port\s+(\d+)', command_line)
        if match and port_has_client(int(match.group(1))):
            return None
    return owner

def reclaim_stale_server_port(port, kind=socket.SOCK_STREAM):
    """Close an abandoned simulator server on *port*; note or None."""
    owner = stale_server_owner(port, kind)
    if owner is None:
        return None
    try:
        subprocess.run(['taskkill', '/PID', str(owner), '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if port_bind_error(port, kind) is None:
            return ('Port {0} was held by a leftover Hulk Hard server '
                    '(PID {1}, no client); closed it and reused {0}.'
                    ).format(port, owner)
        time.sleep(0.2)
    return None

def choose_local_port(preferred, kind=socket.SOCK_STREAM,
                      label='the local server', reclaim=False):
    """Return (port, note) for a localhost port that can actually bind.

    Windows refuses some idle ports with WinError 10013 when the range is
    reserved for Hyper-V/WSL or a VPN shim (``netsh int ipv4 show
    excludedportrange protocol=tcp``); other machines simply have 2048 in
    use.  Keep the requested port when it binds, otherwise move to the
    practice fallbacks and finally let the OS assign an ephemeral port, so
    one blocked port can no longer abort the launcher with a traceback.
    """
    error = port_bind_error(preferred, kind)
    if error is None:
        return preferred, None
    if reclaim:
        closed = reclaim_stale_server_port(preferred, kind)
        if closed is not None and port_bind_error(preferred, kind) is None:
            return preferred, closed
    note = 'Port {0} for {1} is not usable here ({2}); '.format(
        preferred, label, error)
    for candidate in PORT_FALLBACKS:
        if candidate == preferred:
            continue
        closed = (reclaim_stale_server_port(candidate, kind)
                  if reclaim else None)
        if port_bind_error(candidate, kind) is None:
            if closed is not None:
                return candidate, note + closed
            return candidate, note + 'using {0} instead.'.format(candidate)
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(('127.0.0.1', 0))
        candidate = sock.getsockname()[1]
    return candidate, note + 'using {0} instead.'.format(candidate)

def session(argv=None,show_editor=True):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--demo', action='store_true',
                        help='autoplay the fight for watch-only verification')
    parser.add_argument('--diagnostics', action='store_true',
                        help='record read-only native tile/actor/player state at 1 Hz')
    parser.add_argument('--sbpe', type=Path, default=SBPE)
    parser.add_argument('--sbpe-plugins', action='store_true',
                        help='load installed SBPE gameplay/UI plugins in a separate offline profile')
    parser.add_argument('--sbpe-direct', action='store_true',
                        help='use the installed SBPE folder itself: every '
                             'installed plugin and its own config.ini')
    parser.add_argument('--show-green-squids', action='store_true',
                        help='with --sbpe-direct, temporarily disable the '
                             'squid_prediction3 hide_green_squids option so '
                             'the native green summon flight stays visible')
    parser.add_argument('--game', type=Path)
    parser.add_argument('--port', type=int, default=2048)
    parser.add_argument('--bridge-port', type=int, default=21550)
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--no-gear-editor', action='store_true',
                        help='do not start the ~ / F8 overlay editor')
    parser.add_argument('--server-python', type=Path,
                        help='Python 3.10+ for the 60 Hz server loop; explicit '
                             'path or HULK_SERVER_PYTHON overrides the bundled CPython')
    parser.add_argument('--map', default=None,
                        help='map pack from maps/ (default: the map saved in '
                             'game_settings.json, else egy_hulk)')
    parser.add_argument('--player-type',type=int,choices=(1,2,3,5),default=1)
    parser.add_argument('--terrain-variant', default=None,
                        help='lab_hard captured terrain variant name; '
                             'default picks a random captured layout')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        raise SystemExit('The simulator requires Windows and the Windows StarBreak client.')
    from localserver.runtime_content import require_runtime_content
    require_runtime_content(LAB)
    configured_game = game_from_sbpe_config(args.sbpe)
    game_dir = args.game or (configured_game.parent if configured_game else GAME)
    client = game_dir / 'mvmmoclient_local2.exe'
    if not client.is_file() and configured_game:
        client = configured_game
    if not client.is_file() or client.name.lower() != 'mvmmoclient_local2.exe':
        client = game_dir / 'mvmmoclient_local2.exe'
    prepare_local_client(game_dir)
    py32 = args.sbpe / 'pypy' / 'pypy3.exe'
    args.map, map_source = resolve_map(args.map)
    map_info = game_settings.map_info(args.map)
    if args.map == game_settings.ORIGINAL_MAP:
        create_world = (LAB / 'data' / 'catalogue' /
                        'real_hulkboss_createworld_flags.hex')
    else:
        map_dir = Path(game_settings.map_dir(args.map))
        create_world = map_dir / 'createworld.hex'
        if args.map == 'lab_hard':
            from localserver.maps.lab_hard.terrain import choose_variant
            create_world = Path(choose_variant(str(map_dir),
                                               args.terrain_variant))
            print('Terrain variant: {0}'.format(create_world.stem), flush=True)
    pvid, zone = map_info['pvid'], map_info['zone']
    print('Map: {0} [{1}]'.format(
        map_info.get('title') or args.map, map_source), flush=True)
    required = [client, py32, args.sbpe / 'build' / 'remote.bin',
                create_world,
                LAB / 'data/catalogue/characters.json',
                LAB / 'data/catalogue/item_descriptions.jsonl',
                LAB / 'userdata/practice_loadout.json',
                LAB / 'userdata/class_loadouts.json',
                LAB / 'data/catalogue/class_players.json']
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit('Missing required files:\n' + '\n'.join(missing))

    from patch_client_bounds import GUARDS, jump_patch
    from make_local_client import parse_pe_sections, va_to_offset
    binary = client.read_bytes()
    image_base, sections = parse_pe_sections(binary)
    if any(binary[va_to_offset(sections,image_base,va):
                  va_to_offset(sections,image_base,va)+6] != jump_patch(va)
           for va in GUARDS):
        raise SystemExit('Local client has an invalid bounds patch. Run python tools/patch_client_bounds.py first.')
    server_python, server_kind, server_version = choose_server_python(
        args.server_python)
    print('Server interpreter: ' + server_python + ' (' + server_kind
          + ' ' + server_version + ')', flush=True)
    if server_kind != 'cpython':
        print('Note: PyPy runs the fixed 60 Hz step loop about 2% slower '
              'than real time; install CPython or set --server-python for '
              'exact timing.', flush=True)

    if args.check:
        for port, kind, label in ((args.port, socket.SOCK_STREAM,
                                   'game server'),
                                  (args.bridge_port, socket.SOCK_DGRAM,
                                   'SBPE bridge')):
            error = port_bind_error(port, kind)
            if error is not None:
                print('Port {0} ({1}) is not usable here: {2}'.format(
                    port, label, error), flush=True)
                leftover = stale_server_owner(port, kind)
                if leftover is not None:
                    print('A leftover Hulk Hard server (PID {0}) holds it; '
                          'a real run would close it and reuse {1}.'.format(
                              leftover, port), flush=True)
                else:
                    print('A real run would move to another port automatically.',
                          flush=True)
    else:
        args.port, note = choose_local_port(args.port, socket.SOCK_STREAM,
                                            'the game server',
                                            reclaim=True)
        if note:
            print(note, flush=True)
        args.bridge_port, note = choose_local_port(args.bridge_port,
                                                   socket.SOCK_DGRAM,
                                                   'the SBPE bridge',
                                                   reclaim=True)
        if note:
            print(note, flush=True)
    if args.diagnostics:
        os.environ['HARDLAB_LIVE_DIAGNOSTICS'] = str(
            Path(args.runtime_dir) / 'native-state.jsonl' if args.runtime_dir
            else LAB / 'runtime' / 'native-state.jsonl')
    if args.sbpe_direct and args.sbpe_plugins:
        raise SystemExit('Use either --sbpe-direct or --sbpe-plugins, not both.')
    finish_direct = None
    if args.sbpe_direct:
        installed = InstalledSbpeRuntime(args.sbpe, args.bridge_port,
                                         game_dir=game_dir,
                                         diagnostics=args.diagnostics,
                                         show_green_squids=args.show_green_squids)
        runtime = installed.prepare(write=not args.check)
        finish_direct = installed.finish
        print('SBPE install used directly: ' + str(installed.root), flush=True)
        print('Plugins: ' + str(installed.root / 'plugins')
              + ' (every enabled plugin loads)', flush=True)
        print('Config: ' + str(installed.config_path), flush=True)
        if installed.copied:
            print('Bridge plugin updated: ' + ' '.join(installed.copied),
                  flush=True)
        if installed.backup is not None:
            print('Config backup: ' + str(installed.backup), flush=True)
        print('Ordinary SBPE sessions stay dormant without the '
              'HARDLAB_LIVE_SESSION marker.', flush=True)
    else:
        runtime = prepare_runtime(args.sbpe, diagnostics=args.diagnostics,
                                  runtime=args.runtime_dir,
                                  bridge_port=args.bridge_port,
                                  user_plugins=args.sbpe_plugins,
                                  game_dir=game_dir)
        print('Offline runtime: ' + str(runtime), flush=True)
        if args.sbpe_plugins:
            print('Installed SBPE plugins enabled. Local settings: '
                  + str(runtime / 'config.ini'), flush=True)
    if args.check:
        print('Preflight OK. No game or server started.')
        return
    logs = LAB / 'userdata' / 'logs' / ('session-' + time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1000000000))
    logs.mkdir(parents=True, exist_ok=True)
    server = None
    native_recording = None
    gear_editor = None
    gear_editor_log = None
    restart=None
    try:
        from localserver.replay import CLIENT_MAPS
        recording_settings=LAB/'userdata/recording.json'
        recording_enabled=os.environ.get('SB_RECORD_REPLAY','1')=='1'
        if recording_settings.is_file():
            recording_enabled=recording_enabled and json.loads(recording_settings.read_text(encoding='utf-8-sig')).get('record_replays',True) is not False
        if args.map in CLIENT_MAPS:
            try:
                from tools.native_recording import NativeRecording
                from localserver.battle_records import RecordStore
                native_recording=NativeRecording(RecordStore().directory,client,registry=LAB/'userdata/native-control.json',recording=recording_enabled,health_path=logs/'bridge-status.json')
                print('Native replay recording ready.' if recording_enabled else 'Native controls ready; replay recording disabled.',flush=True)
            except Exception as error:
                print('Native replay recording unavailable: '+type(error).__name__,flush=True)

        if not args.no_gear_editor:
            editor_python = choose_editor_python()
            if editor_python is None:
                print('Equipment window requires the bundled 64-bit Python 3.12 '
                      'runtime (or use --no-gear-editor).',
                      flush=True)
            else:
                gear_editor_log_path = logs / 'gear-editor.log'
                gear_editor_log = gear_editor_log_path.open(
                    'w', encoding='utf-8', errors='replace')
                gear_editor = subprocess.Popen([
                    editor_python, '-u', '-m', 'tools.desktop_view',
                    *(['--show'] if show_editor else []),
                    '--owner-pid', str(os.getpid())],
                    cwd=str(LAB), stdout=gear_editor_log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

                time.sleep(0.45)
                if gear_editor.poll() is not None:
                    gear_editor_log.flush()
                    tail = gear_editor_log_path.read_text(
                        encoding='utf-8', errors='replace')[-2000:]
                    if gear_editor.returncode == 0:
                        print('Gear editor: an existing panel was asked to '
                              'show itself; press ~ or F8 if it is hidden. '
                              'Log: ' + str(gear_editor_log_path),
                              flush=True)
                    else:
                        print('Gear editor exited during startup (exit {0}). '
                              'Log: {1}'.format(
                                  gear_editor.returncode,
                                  gear_editor_log_path), flush=True)
                        if tail.strip():
                            print(tail, flush=True)
                else:
                    print('Gear editor: ~ or F8 shows/hides the overlay; '
                          'log: ' + str(gear_editor_log_path), flush=True)
        with (logs / 'server.log').open('w') as output:
            command = [
                server_python, '-u', '-m', 'localserver.server',
                '--mode', 'hulk', '--live', '--map', args.map,
                '--pvid', pvid, '--zone', zone, '--gdv',
                str(game_settings.map_info(args.map).get('gdv', 1)),
                '--port', str(args.port), '--bridge-port', str(args.bridge_port),
                '--create-world-hex', str(create_world),
                '--characters', str(required[4]), '--items', str(required[5]),
                '--data-version', str(game_dir / 'data' / 'texture' / 'dataVersion.bpb'),
                '--class-loadouts', str(required[7]),
                '--player-type', str(args.player_type),
                '--player-payloads', str(required[8]),
                '--no-time-echo']
            if args.terrain_variant:
                command.extend(['--terrain-variant',args.terrain_variant])
            if args.demo:
                command.extend(['--live-demo-damage', '--live-auto-interact',
                                '--live-force-demo'])
            server_env=dict(os.environ)
            server_env.pop('SB_REPLAY_ATTEMPT',None)
            server_env['SB_RECORD_REPLAY']='0'
            if native_recording and recording_enabled:
                server_env.update(SB_RECORD_REPLAY='1',SB_REPLAY_ATTEMPT=native_recording.identifier)
            server = subprocess.Popen(command, cwd=str(LAB), stdout=output,env=server_env,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError('Server failed. See ' + str(logs / 'server.log'))

                if 'listening' in (logs / 'server.log').read_text(errors='replace').lower():
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('Server did not become ready: ' + str(logs))
            print('Practice server ready. Close the game to stop. Logs: ' + str(logs), flush=True)

            client_env = dict(os.environ)
            if native_recording:client_env['HARDLAB_NATIVE_REPLAY']=json.dumps(native_recording.config,separators=(',',':'))
            client_env['HARDLAB_KEYBINDINGS'] = str(LAB / 'userdata' / 'keybindings.json')
            client_env['HARDLAB_BRIDGE_STATUS'] = str(logs / 'bridge-status.json')
            client_env['PYTHONIOENCODING'] = 'cp936:replace'
            client_env['PYTHONLEGACYWINDOWSSTDIO'] = '1'
            client_log = logs / 'client-launch.log'
            with client_log.open('w', encoding='utf-8', errors='replace') as out:
                client_port=native_recording.gate(args.port) if native_recording else args.port
                client_command = [str(py32), str(LAB / 'tools/run_live_client.py'),
                     '--sbpe', str(args.sbpe), '--client', str(client),
                     '--server', '127.0.0.1:' + str(client_port),
                     '--runtime-dir', str(runtime), '--keep']
                client_command.extend(['--log-file',str(logs/'live-client.log')])
                owned_stop=logs/'stop-owned-client'
                client_command.extend(['--stop-file',str(owned_stop)])
                result = subprocess.Popen(client_command,
                    env=client_env, stdin=subprocess.DEVNULL,
                    stdout=out, stderr=subprocess.STDOUT)
                while result.poll() is None:
                    if args.stop_file and args.stop_file.exists():
                        owned_stop.touch();break
                    if native_recording and native_recording.restart_request:
                        restart=dict(native_recording.restart_request)
                        owned_stop.touch();break
                    time.sleep(.1)
                result.wait(timeout=30)
            if result.returncode != 0:
                tail = client_log.read_text(errors='replace')[-2000:]
                raise RuntimeError(
                    'Client launcher exited with {0}. Log: {1}\n{2}'.format(
                        result.returncode, client_log, tail))
    finally:
        if restart is None and gear_editor is not None and gear_editor.poll() is None:
            gear_editor.terminate()
            try:
                gear_editor.wait(timeout=5)
            except Exception:
                pass
        if gear_editor_log is not None:
            gear_editor_log.close()
        if server is not None and server.poll() is None:
            server.terminate()
            server.wait(timeout=10)
        if native_recording is not None:
            try:native_recording.close()
            except Exception as error:print('Native recording could not be sealed: '+type(error).__name__,flush=True)
        if finish_direct is not None:
            finish_direct()
    return restart


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    first=True
    while True:
        restart=session(arguments,show_editor=first)
        if not restart:return
        first=False
        for flag,value in (('--map',restart['map']),('--player-type',str(restart['shell']))):
            for index in range(len(arguments)-1,-1,-1):
                if arguments[index]==flag:del arguments[index:index+2]
                elif arguments[index].startswith(flag+'='):del arguments[index]
            arguments.extend([flag,value])

if __name__ == '__main__':
    main()
