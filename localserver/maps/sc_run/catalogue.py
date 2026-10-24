"""SC room geometry, decoration and object templates for the local runtime."""
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3] / 'maps/sc_run'


class ScContent:
    def __init__(self, root=None, *, generate=True):
        self.root = Path(root or ROOT)
        self.manifest = self._read('content.json')
        self.dynamic_templates = self._read('dynamic-templates.json')
        path = self.root / 'dynamic-template-variants.json'
        self.dynamic_template_variants = (json.loads(path.read_text(encoding='utf8'))
                                          if path.exists() else {})
        self.dynamic_variants = self.dynamic_template_variants
        streams = self._read('mledge-stream.json') if 'scmid01' in self.manifest['templates'] else None
        self.templates = {}
        for descriptor, files in self.manifest['templates'].items():
            self.templates[descriptor] = [self._recipe(self._read(file)) for file in files]
            if descriptor == 'scmid01' and streams:
                for recipe in self.templates[descriptor]:
                    recipe.setdefault('metadata', {})['sc_mledge_stream'] = copy.deepcopy(streams)
                    # The source snapshot was already streaming. Fresh entry
                    # must be empty in CreateWorld itself, not show resident
                    # platforms for one frame before a runtime destroy delta.
                    resident = {oid for oid, props in recipe['objects'].items()
                                if props.get('vid', '').startswith('sc.mledge')}
                    for oid in resident:
                        recipe['objects'].pop(oid)
                        recipe['creators'].pop(oid, None)
                    server = recipe['world']['worldstate_']['serverworld_']
                    server['obj_'] = [row for row in server.get('obj_', []) if row['objid_'] not in resident]
        for alias, descriptor in self.manifest.get('aliases', {}).items():
            self.templates[alias] = self.templates[descriptor]
        self.generator = None
        self.monk_population = None
        if generate and 'scmid04' in self.templates:
            from .monks import MonkPopulation
            self.monk_population = MonkPopulation(self.templates['scmid04'])
        if generate and (self.root/'modules/pool.json').is_file():
            from .generator import ScRoomGenerator
            self.generator = ScRoomGenerator(self.root)

    def _read(self, filename):
        return json.loads((self.root / filename).read_text(encoding='utf8'))

    @staticmethod
    def _recipe(value):
        for field in ('objects', 'creators', 'links', 'switches'):
            value[field] = {int(key): item for key, item in value.get(field, {}).items()}
        for props in value['objects'].values():
            # The source core room was reached after another Gen had died.
            # New-run progress starts empty; apply_progress owns later changes.
            if props.get('vid') == 'sc.taskfull':
                props['vid'] = 'sc.taskempty'
        # The recipe's wire document is also consumed by previews/tools.
        # Apply the same fresh-run boundary to that duplicate object table.
        for row in value['world']['worldstate_']['serverworld_'].get('obj_', []):
            props = row.get('props_', {})
            if props.get('vid_') == 'sc.taskfull':
                props['vid_'] = 'sc.taskempty'
        for field in ('sc_surn_counts', 'sc_mledge_velocity'):
            if field in value.get('metadata', {}):
                value['metadata'][field] = {int(key): item for key, item in value['metadata'][field].items()}
        from .doors import bind_triggers
        bind_triggers(value)
        return value

    def recipe(self, descriptor, seed=0):
        variants = self.templates[descriptor]
        recipe = copy.deepcopy(variants[int(seed) % len(variants)])
        # Room geometry is rolled before the level graph connects its doors.
        # A persistent run then caches this complete instance across visits.
        recipe = self.generator.generate(recipe, seed) if self.generator is not None else recipe
        return self.monk_population.populate(recipe, seed) if self.monk_population is not None else recipe

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def props_variants(self, vid):
        return copy.deepcopy(self.dynamic_template_variants.get(vid, [self.dynamic_templates[vid]]))
