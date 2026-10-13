'Compiled EUB content and existing Dumb capability adapters; no captures.'
import copy
import json
from pathlib import Path

from ... import pb_layout
from ..dumb_common.scene import normalize
from ..dumb_boss.scene import DumbBossPack
from ..dumb_mid.scene import DumbMidPack
from tools.make_create_world import wrap_compress

ROOT=Path(__file__).resolve().parents[3]/'maps/eub_run'

class EubNpcPack(DumbBossPack):
    """Real room terrain/templates with existing species/projectile capabilities.

    This supplies the data contract to DumbActorHost. ExplorationEnemies and
    ExplorationMechanics provide room-aware motion and device controllers;
    exchanging this data pack alone does not provide their behaviour.
    """
    def __init__(self,owner,room):
        super().__init__(owner.root/'encounters/boss')
        self.owner=owner
        self.templates=copy.deepcopy(room['objects'])
        self.scene.update(bounds_mp=room['metadata']['bounds_mp'],
            elevators=room['mechanics']['elevators'],telepads=room['mechanics']['telepad_pairs'],
            doors=[oid for oid,p in room['objects'].items() if p.get('vid')=='dumb.closedDoor'])
        self.archetypes.update(copy.deepcopy(owner.dynamic_templates))
        self.room_metadata=copy.deepcopy(room['metadata'])
        self.behaviour=dict(evidence='SIM exploration capability binding; no captured attack timetable',
            implemented=['current-room aircraft bounds and surfaces','Dumb NPC actions and native projectile motion',
                         'tower/shieldturret/robosweeper/laser/spawner controllers','native paired switch MOVE and lifts'],
            pending=['normal native acceptance','FIT-only switch/telepad linkage','unknown original activation/cooldown gates and RNG'])

    def species_props(self,species):
        vid=str(species) if str(species).startswith('dumb.') else 'dumb.'+str(species)
        if vid in self.owner.dynamic_templates:return self.owner.props(vid)
        return super().species_props(species)

    def projectile_props(self,key):
        vid=str(key) if str(key).startswith('dumb.') else 'dumb.'+str(key)
        return copy.deepcopy(self.archetypes.get(vid,{}))

    def bind_host_geometry(self,host):
        """Bind real collision and camera limits, without changing shared code."""
        host.bounds=tuple(self.scene['bounds_mp'])
        host.solids=[(*[p.get(k,0) for k in ('xmp','ymp','wmp','hmp')],p['terraintype'])
                     for p in self.templates.values() if p.get('terraintype') in (1,2)]
        host.walls=[row for row in host.solids if row[4]==1]
        host.npc_ceiling=host.bounds[1]
        host.npc_floor=host.bounds[3]
        host.npc_regions={}
        return dict(bounds_mp=list(host.bounds),surfaces=len(host.solids),
                    limitation='Dumb common fly_step still has a fixed bottom clamp; host-aware movement adapter remains required')

class EubRunPack:
    def __init__(self,root=None,generate=None):
        self.root=Path(root or ROOT)
        self.manifest=json.loads((self.root/'scene.json').read_text(encoding='utf8'))
        self.dynamic_templates=json.loads((self.root/'dynamic-templates.json').read_text(encoding='utf8'))
        self.dynamic_variants=json.loads((self.root/'dynamic-template-variants.json').read_text(encoding='utf8'))
        self.wave_rules=json.loads((self.root/'wave-chamber-rules.json').read_text(encoding='utf8'))
        self._rooms={role:[json.loads((self.root/path).read_text(encoding='utf8')) for path in paths]
                     for role,paths in self.manifest['rooms'].items()}
        config_path=self.root/'modules/enabled.json'
        config=json.loads(config_path.read_text(encoding='utf8')) if config_path.exists() else {}
        self.generate=bool(config.get('enabled',False)) if generate is None else bool(generate)
        self._generator=None
        if self.generate:
            from .generator import EubModuleGenerator
            self._generator=EubModuleGenerator(self.root)
            if config and config.get('content_version')!=self._generator.data['content_version']:
                raise ValueError('EUB published module pool/enabled version mismatch')

    @property
    def room_names(self):return tuple(self._rooms)

    def _role(self,role):
        if isinstance(role,int):return self.manifest['route'][role]
        if str(role).startswith('floor'):
            return self.manifest['route'][int(str(role).replace('floor','').lstrip('_'))]
        return role

    def room(self,role,seed=0):
        role=self._role(role);variants=self._rooms[role]
        result=copy.deepcopy(variants[int(seed)%len(variants)])
        for field in ('objects','creators','links','switches'):
            result[field]={int(k):v for k,v in result.get(field,{}).items()}
        if self._generator is not None:
            disabled=self._generator.data.get('disabled_roles',{}).get(role)
            if disabled:
                result['metadata']['generation']=dict(mode='capture-native-disabled',reason=disabled,
                    seed=int(seed),base_variant=result['variant'],pool_version=self._generator.data['content_version'],
                    content_version=self.manifest['content_version'])
            else:result=self._generator.generate(result,seed)
        return result

    def encounter_room(self,role,seed=0):
        room=self.room(role,seed)
        if room['role'] not in ('mid','boss'):raise ValueError('Not an existing EUB encounter: '+str(role))
        removed=room['metadata'].get('encounter_managed_initial_ids',[])
        for oid in removed:room['objects'].pop(oid,None);room['creators'].pop(oid,None)
        room['metadata']['controller_initialization_removed_ids']=list(removed)
        room['metadata']['object_count']=len(room['objects'])
        self._set_objects(room['world'],room['objects'],room)
        return room

    def encounter_pack(self,role,seed=0):
        room=self.encounter_room(role,seed)
        pack=DumbMidPack(self.root/'encounters/mid') if room['role']=='mid' else DumbBossPack(self.root/'encounters/boss')
        pack.templates=copy.deepcopy(room['objects'])
        pack.scene['bounds_mp']=list(room['metadata']['bounds_mp'])
        pack.scene['doors']=[oid for oid,p in room['objects'].items() if p.get('vid')=='dumb.closedDoor']
        pack.scene['elevators']=room['mechanics']['elevators']
        if room['role']=='mid':pack.scene['telepads']=room['mechanics']['telepad_pairs']
        return pack

    def npc_pack(self,role,seed=0):return EubNpcPack(self,self.room(role,seed))

    def plan(self,seed,min_each=1):
        from .route import EubRoutePlanner
        return EubRoutePlanner(self.root,min_each=min_each).plan(seed)

    def for_run(self,seed,min_each=1):
        from .route import EubPlannedPack
        return EubPlannedPack(self,seed,min_each=min_each)

    def props(self,vid):
        try:return copy.deepcopy(self.dynamic_templates[vid])
        except KeyError as error:raise KeyError('EUB compiled dynamic template missing: '+str(vid)) from error

    def props_variants(self,vid):return copy.deepcopy(self.dynamic_variants[vid])

    @staticmethod
    def _set_objects(world,objects,room):

        rows=[]
        for oid,p in sorted(objects.items(),key=lambda item:int(item[0])):
            row=dict(objid_=int(oid),props_=normalize('Properties',p))
            rows.append(row)
        world['worldstate_'].pop('clientworld_',None)
        world['worldstate_']['serverworld_']['ownerid_']=1
        world['worldstate_']['serverworld_']['obj_']=rows
        world['worldid_']=2
        world['spawnobj_']=dict(worldid_=1,objid_=room['metadata']['spawn_marker_id'])

    def world_blob(self,role,seed=0,objects=None,spawn=None):
        room=self.room(role,seed)
        chosen=copy.deepcopy(room['objects'] if objects is None else objects)
        chosen={int(k):v for k,v in chosen.items()}
        marker=room['metadata']['spawn_marker_id']
        if marker in chosen and not chosen[marker].get('spawn'):marker=max(chosen,default=0)+1
        room['metadata']['spawn_marker_id']=marker
        point=room['spawn'] if spawn is None else spawn;w,h=room['metadata']['spawn_footprint_mp']
        chosen[marker]=dict(xmp=round(point[0]+w/2),ymp=round(point[1]+h),wmp=0,hmp=0,draw=False,spawn=True)
        self._set_objects(room['world'],chosen,room)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld',room['world']))
