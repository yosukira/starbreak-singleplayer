"""The shared equipment editor backed by the native player's settings."""
import copy
import hashlib
import json
from pathlib import Path

from localserver import paths, projectile_speeds
from tools.web_workspace import Workspace, SCHEMA, CLASSES, atomic_json


class DesktopWorkspace(Workspace):
    files = ('class_loadouts.json', 'class_loadout_overrides.json', 'game_settings.json')

    def __init__(self, directory, source=None):
        super().__init__(directory, source)
        self._source_digest = None
        self.refresh()

    def refresh(self):
        """Import subsequent native changes without replacing an editor draft."""
        with self.lock:
            digest = hashlib.sha256(b''.join((self.source / name).read_bytes() for name in self.files)).hexdigest()
            if digest == self._source_digest:
                return
            loadouts, overrides, settings = [json.loads((self.source / name).read_text(encoding='utf-8-sig')) for name in self.files]
            current = self.read()
            value = self.validate(dict(schema=SCHEMA, defaults_revision=2, revision=current['revision'],
                loadouts={str(k): loadouts[str(k)] for k in CLASSES},
                projectile_speeds=overrides.get(projectile_speeds.KEY, {}),
                turrets=loadouts.get('_turret_counts', overrides.get('_turret_counts', {})),
                missiles=loadouts.get('_turret_missiles', overrides.get('_turret_missiles', {})), settings=settings))
            if value != current:
                value['revision'] += 1
                atomic_json(self.path, value)
            self._source_digest = digest

    def editor(self):
        self.refresh()
        value = super().editor()
        value['desktop'] = True
        return value

    def save(self, document):
        with self.lock:
            self.refresh()
            value = super().save(document)
            loadouts = json.loads((self.source / 'class_loadouts.json').read_text(encoding='utf-8-sig'))
            overrides = json.loads((self.source / 'class_loadout_overrides.json').read_text(encoding='utf-8-sig'))
            for key, row in value['loadouts'].items():
                loadouts[key] = copy.deepcopy(row)
                overrides[key] = dict(mk_=row['mk_'], impslots_=row['impslots_'], **{
                    str(item['slotpos_']): dict(type_=item['type_'], statboosts_=item['statboosts_']) for item in row['items_']})
            for name, content in (('_turret_counts', value['turrets']), ('_turret_missiles', value['missiles'])):
                loadouts[name] = content
                overrides[name] = content
            overrides[projectile_speeds.KEY] = value['projectile_speeds']
            # The loadout replacement is the existing server's reload signal;
            # write its dependencies first and keep recoverable previous files.
            updates = [('class_loadout_overrides.json', overrides), ('game_settings.json', value['settings']),
                       ('practice_loadout.json', loadouts['1']), ('class_loadouts.json', loadouts)]
            for name, content in updates:
                target = self.source / name
                if target.is_file():
                    target.with_suffix('.json.bak').write_bytes(target.read_bytes())
                atomic_json(target, content)
            self._source_digest = hashlib.sha256(b''.join((self.source / name).read_bytes() for name in self.files)).hexdigest()
            return value

    def preferences(self):
        result = super().preferences()
        path = self.source / 'keybindings.json'
        if path.is_file():
            from tools.web_workspace import normalize_web_keys
            result['keys'] = normalize_web_keys(dict(result['keys'], **json.loads(path.read_text(encoding='utf-8-sig'))))
        recording = self.source / 'recording.json'
        if recording.is_file():
            result['record_replays'] = json.loads(recording.read_text(encoding='utf-8-sig')).get('record_replays', True) is not False
        return result

    def save_preferences(self, value):
        result = super().save_preferences(value)
        atomic_json(self.source / 'keybindings.json', result['keys'])
        atomic_json(self.source / 'recording.json', dict(record_replays=result['record_replays']))
        return result

    def start(self, document):
        self.refresh()
        map_id = document.get('map')
        from localserver import game_settings
        if map_id not in game_settings.available_maps() or document.get('shell') not in CLASSES:
            raise ValueError('Select a valid encounter and class')
        return dict(starting=True, map=map_id, shell=document['shell'])
