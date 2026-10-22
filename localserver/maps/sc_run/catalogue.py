"""SC room geometry, decoration and object templates for the local runtime."""
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3] / 'maps/sc_run'


class ScContent:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.manifest = self._read('content.json')
        self.dynamic_templates = self._read('dynamic-templates.json')
        path = self.root / 'dynamic-template-variants.json'
        self.dynamic_template_variants = (json.loads(path.read_text(encoding='utf8'))
                                          if path.exists() else {})
        self.dynamic_variants = self.dynamic_template_variants
        self.templates = {}
        for descriptor, files in self.manifest['templates'].items():
            self.templates[descriptor] = [self._recipe(self._read(file)) for file in files]
        for alias, descriptor in self.manifest.get('aliases', {}).items():
            self.templates[alias] = self.templates[descriptor]

    def _read(self, filename):
        return json.loads((self.root / filename).read_text(encoding='utf8'))

    @staticmethod
    def _recipe(value):
        for field in ('objects', 'creators', 'links', 'switches'):
            value[field] = {int(key): item for key, item in value.get(field, {}).items()}
        for field in ('sc_surn_counts', 'sc_mledge_velocity'):
            if field in value.get('metadata', {}):
                value['metadata'][field] = {int(key): item for key, item in value['metadata'][field].items()}
        return value

    def recipe(self, descriptor, seed=0):
        variants = self.templates[descriptor]
        return copy.deepcopy(variants[int(seed) % len(variants)])

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def props_variants(self, vid):
        return copy.deepcopy(self.dynamic_template_variants.get(vid, [self.dynamic_templates[vid]]))
