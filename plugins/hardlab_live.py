"""Live Hulk Hard simulator bridge plugin (SBPE).

Place this file plus ``_hardlab_livebridge.py`` in the SBPE plugins folder.
Press **F6** to enable/disable the bridge.  While enabled the plugin sends
player telemetry (position/hp/interact) and player->enemy damage events to
the local live server over UDP (default 127.0.0.1:21550) and shows a small
status line in the top-left corner.

The plugin forwards native feedback. In the Dumb boss practice room it also
applies local player teleports, whose effects are mirrored by the server.

Target runtime: Python 3.5 / PyPy3 embedded in SBPE.  No f-strings.
"""

import importlib
import logging
import os
import sys
import time

try:
    from _remote import ffi, lib
    from manager import PluginBase
    import util
except ImportError:
    ffi = lib = util = None
    PluginBase = object

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

livebridge = importlib.reload(importlib.import_module('_hardlab_livebridge'))
teleports = importlib.reload(importlib.import_module('_dumb_boss_teleports'))
wave_teleports = importlib.reload(importlib.import_module('_dumb_wave_teleports'))

VERSION = '0.1.4'
LOG = logging.getLogger('hardlab_live')

F6 = 65

SESSION_ENV = 'HARDLAB_LIVE_SESSION'

def in_live_session(environ=None):
    source = os.environ if environ is None else environ
    return str(source.get(SESSION_ENV, '')).strip().lower() in (
        '1', 'yes', 'true', 'on')

class Plugin(PluginBase):
    def onInit(self):
        try:
            from . import _practice_keybindings as keybindings
        except (ImportError, ValueError, SystemError):
            import _practice_keybindings as keybindings
        self.keybindings = keybindings
        self.key_reader = keybindings.Reader()
        self.key_reader.refresh()
        self._pending_interact_edge = False
        self.config.option('enabled', True, 'bool')
        self.config.option('toggle_key', F6, 'int')
        self.config.option('host', '127.0.0.1', 'str')
        self.config.option('port', 21550, 'int')
        self.config.option('telemetry_hz', 60.0, 'float')
        self.config.option('diagnostics_file', '', 'str')
        self.config.option('interact_scancode', 0, 'int')

        self.config.option('teleport_test_at', '', 'str')

        self.config.option('hold_seconds', 2.0, 'float')
        self.config.option('hud_visible', True, 'bool')
        self.config.option('hud_size', 14, 'int')
        self.config.option('hud_color', 0xffffffff, 'color')

        self.config.option('keep_time_reserve', True, 'bool')

        self.session = in_live_session()
        self.bridge = livebridge.LiveBridge(
            ffi, lib, util, self.refs, port=int(self.config.port),
            host=str(self.config.host))
        self.bridge_error = None
        self._teleport_enabled = None
        self._teleport_times = []
        for part in str(self.config.teleport_test_at).split(','):
            try:
                seconds = float(part)
                if seconds > 0:
                    self._teleport_times.append(seconds)
            except (TypeError, ValueError):
                continue

        self._teleport_latched = False
        self.active = False
        self.status = 'bridge: off (F6)'
        self.last_send = 0.0
        self.next_send = 0.0
        self.interval = 1.0 / max(1.0, float(self.config.telemetry_hz))
        self.old_toggle = False
        self.last_diagnostics = 0.0
        self.txt = (util.PlainText(font='HemiHeadBold')
                    if util is not None else None)
        if not self.session:

            self.status = ''
            LOG.info('dormant (no %s)', SESSION_ENV)
        elif bool(self.config.enabled):
            self._enable()
        self.native_capture=None
        if self.session:
            try:
                recorder=importlib.import_module('_native_replay_capture')
                self.native_capture=recorder.create(ffi,lib,self.refs)
                if self.native_capture is not None:self.bridge.feedback_sink=self.native_capture.divert_feedback
            except Exception as error:
                LOG.warning('native recording unavailable: %s',type(error).__name__)

    def _enable(self):
        try:
            self._pending_interact_edge = False
            self.bridge._old_interact = True
            self.bridge._interaction_held_blocked = True
            self.bridge._hold_since.clear()
            self.bridge._hold_fired.clear()
            self.bridge.enable()
            self.active = True
            self.status = 'bridge: link...'
            if self.bridge.hook_error:
                self.status = 'bridge: telemetry (hook: {0})'.format(
                    self.bridge.hook_error)
        except Exception as error:
            self.bridge_error = type(error).__name__ + ': ' + str(error)
            self.active = False
            self.status = 'bridge error: ' + self.bridge_error
            LOG.error('live bridge enable failed: %s', self.bridge_error)

    def _disable(self):
        self._pending_interact_edge = False
        self.bridge.disable()
        self.active = False
        self.status = 'bridge: off (F6)'

    def _toggle(self):
        if self.active:
            self._disable()
        else:
            self._enable()

    def beforeUpdate(self):
        if self.native_capture is not None:
            branch=self.native_capture.branch
            try:
                self.native_capture.poll(self._replay_post_frame)
                self.bridge.feedback_isolated=self.native_capture.player is not None
                if self.native_capture.last_ack is not None:
                    self.bridge.receive_native_ack(self.native_capture.last_ack)
                    self.native_capture.last_ack=None
                for document in self.native_capture.bridge_documents:
                    self.bridge.receive_native_ack(document)
                self.native_capture.bridge_documents=[]
                if branch is None and self.native_capture.branch is not None:
                    self.bridge._battle_activity=False
                    metrics=getattr(self.bridge,'health_metrics',None)
                    if metrics is not None:metrics.totals.reset(None)
                self.native_capture.before()
            except Exception as error:
                self.native_capture.tape.fail(error)
        if self.session and self.active:
            try:self.bridge.sample_interaction_target()
            except Exception:
                self.bridge._interaction_pre_local=False

    def afterUpdate(self):
        try:self._after_update_live()
        finally:
            if self.native_capture is not None:
                try:self.native_capture.after()
                except Exception as error:self.native_capture.tape.fail(error)

    def _replay_post_frame(self):
        if bool(self.config.keep_time_reserve):self.bridge.top_up_reserve()
        self.bridge.begin_native_frame()

    def _after_update_live(self):
        if not self.session:
            return
        if self.native_capture is not None and self.native_capture.player is not None and self.native_capture.player.blocked():
            state=self.native_capture.player.status()
            self.status='Replay {0}  {1:.1f}/{2:.1f}s'.format(state['state'],state['seconds'],state['duration'])
            return
        try:
            edge = self._edge()
            if edge:
                self._toggle()
            if not self.active:
                return

            if bool(self.config.keep_time_reserve):
                self.bridge.top_up_reserve()
            self.bridge.begin_native_frame()

            if self.bridge.ground_restore_attached():
                self.bridge._drain_ack()
                self.bridge.send_ground_restore(time.time())
            if self.key_reader.refresh():
                self.bridge._hold_since.clear()
                self.bridge._hold_fired.clear()
                self._pending_interact_edge = False
                self.bridge._old_interact = True
                self.bridge._interaction_held_blocked = True
            keyboard = self.key_reader.document
            interact_code = self.keybindings.scancode(keyboard, 'interact')
            if interact_code:
                interact_code = (self.bridge._keyboard_scancode(self.keybindings.keycode(keyboard['interact']))
                                 or interact_code)
            self.bridge.sample_interaction_context(interact_code)
            down, edge = self.bridge.interact_edge(interact_code)
            if self.bridge.interaction_blocked():
                self._pending_interact_edge = False
            else:
                self._pending_interact_edge = self._pending_interact_edge or edge

            if self.bridge.reapply_room_input():
                self._pending_interact_edge = False
            now = time.time()
            if now < self.next_send:
                return
            self.last_send = now

            if now-self.next_send > self.interval*4:
                self.next_send = now+self.interval
            else:
                self.next_send += self.interval
            interact_edge = self._pending_interact_edge
            fixture_edge = self._teleport_test_due(now)
            if fixture_edge:
                interact_edge = True
            if self.bridge.interaction_blocked():

                interact_edge = False
            jumps = []
            if interact_edge and not self._teleport_latched:
                self._teleport_latched = True
                jump = self._dumb_boss_teleport()
                if jump:
                    jumps.append(jump)
            if not down and not fixture_edge:
                self._teleport_latched = False
            shortcuts = []
            for scan, name, char_type, kind in self.keybindings.shortcuts(keyboard):
                if not name.startswith('Num'):
                    scan = self.bridge._keyboard_scancode(self.keybindings.keycode(name)) or scan
                shortcuts.append((scan, name, char_type, kind))
            holds = self.bridge.character_holds(float(keyboard['hold_seconds']), shortcuts)
            document = self.bridge.build_datagram(down, interact_edge,
                                                  holds + jumps)
            self.bridge.seq = document['seq']
            sent_before = self.bridge.sent
            self.bridge.send(document)
            if self.bridge.sent > sent_before:
                self._pending_interact_edge = False
            self.bridge.send_ground_egress(now)
            diagnostics = (str(self.config.diagnostics_file)
                           or os.environ.get('HARDLAB_LIVE_DIAGNOSTICS', ''))
            if diagnostics and now-self.last_diagnostics >= 1.0:
                self.last_diagnostics = now
                with open(diagnostics, 'a', encoding='utf-8') as handle:
                    import json
                    handle.write(json.dumps(self.bridge.native_diagnostics())+'\n')
            if self.bridge.link_ok():
                player = document.get('player') or {}
                self.status = 'live x={0} y={1} hp={2} dmg={3}'.format(
                    player.get('xmp'), player.get('ymp'),
                    player.get('hp'), len(document.get('events') or []))
            else:
                self.status = 'live: waiting for server ack'
        except Exception as error:
            self.status = 'live error: ' + type(error).__name__
            LOG.warning('live bridge update failed: %s', error)

    def _teleport_trace(self, message):
        try:
            import tempfile
            path = os.path.join(tempfile.gettempdir(), 'dumb_teleport_probe.log')
            with open(path, 'a', encoding='utf-8') as handle:
                handle.write('{0:.3f} {1}\n'.format(time.time(), message))
        except Exception:
            pass

    def _teleport_test_due(self, now):
        'Fixture hook: synthesise the configured interact edges.'
        if not self._teleport_times:
            return False
        cw=self.bridge._current_world()
        if cw is None or cw.player==ffi.NULL:
            return False
        if self._teleport_enabled is None:
            self._teleport_enabled = now
        if now - self._teleport_enabled >= self._teleport_times[0]:

            self._teleport_times.pop(0)
            return True
        return False

    def _dumb_boss_teleport(self):
        'Move the local player between the runtime same-side teleporters.'
        if self.bridge.interaction_blocked():
            return None
        if self.bridge.input_context.get('map') == 'eub_run':
            return None
        cw = self.bridge._current_world()
        if cw is None or cw.player == ffi.NULL:
            return None
        music=util.getstr(cw.asWorld.props.music)
        if music not in ('dumb_boss','dumb'):
            return None
        zone=util.getstr(cw.asWorld.props.zone)
        if zone=='dumb_mid':return None
        table=wave_teleports if music=='dumb'else teleports
        player = cw.player.asClientObject.asNativeObject.asWorldObject
        props = player.props
        x, y = int(props.xmp), int(props.ymp)
        w, h = int(props.wmp), int(props.hmp)
        self._teleport_trace('call rect=%s,%s,%s,%s' % (x, y, w, h))
        if w <= 0 or h <= 0:
            return None
        row = (table.match(x,y,w,h,normal=zone=='dumb_normal_wave')
               if table is wave_teleports else table.match(x,y,w,h))
        if row is None:
            self._teleport_trace('no volume matched')
            return None
        vx, vy, vw, vh, source, destination, dx, dy, dw = row
        nx,ny = table.landing(row,w,h)
        if not self.bridge.place_player(nx, ny):
            self._teleport_trace('place_player failed')
            return None
        self._teleport_trace('move %s,%s -> %s,%s pad %s->%s'
                             % (x, y, nx, ny, source, destination))
        LOG.info('dumb_boss teleport %s,%s -> %s,%s pad %s->%s',
                 x, y, nx, ny, source, destination)
        return {'kind': 'teleport', 'from': [x, y], 'to': [nx, ny],
                'source': source, 'destination': destination}

    def _edge(self):
        try:
            states, count = self.bridge.key_states()
            if states == ffi.NULL or F6 >= count:
                return False
            down = bool(states[F6])
        except Exception:
            return False
        edge = down and not self.old_toggle
        self.old_toggle = down
        return edge

    def onPresent(self):
        if not self.session or not bool(self.config.hud_visible):
            return
        try:
            self.txt.text = self.status
            self.txt.size = int(self.config.hud_size)
            self.txt.color = self.config.hud_color
            self.txt.draw(4, 4, anchorX=0)
        except Exception:
            pass

    def onUnload(self):
        if not self.session:
            return
        try:
            self.bridge.uninstall()
        except Exception:
            pass
