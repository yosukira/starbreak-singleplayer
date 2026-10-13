'runtime native HP-removal capability for exact map/VID/mask states.'
from collections.abc import Mapping
import hashlib
import json
import logging
from pathlib import Path

POLICY_PATH=Path(__file__).resolve().parent/'data/native_enemy_death_policy.json'
POLICY_SHA256='1630d1c809276967b0501fe1ab1ca5bd31111350944309442913fa97e44af1ad'
_SCHEMA='native-enemy-death-policy-v1'
_REQUIREMENTS={'hitpoints':'finite_nonnegative_int32','ownertype':2,
               'invulnerable':False,'isinternal':False,'foreignclass':0,
               'exact_captured_removetype':True}
_DEFAULTS={'hitpoints':-1,'removetype':1,'ownertype':0,
           'invulnerable':False,'isinternal':False,'foreignclass':0}
_EVIDENCE=frozenset(('observed_local_hp_destroy','native_property_capability','shared_egy_reference'))
_INVALID=object()

def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate policy field')
        result[key]=value
    return result

def _load_policy(path=POLICY_PATH):
    """Return an immutable allow set, or the empty set on any policy error."""
    try:
        raw=Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=POLICY_SHA256:
            raise ValueError('policy checksum mismatch')
        document=json.loads(raw.decode('utf8'),object_pairs_hook=_unique_object)
        if (not isinstance(document,dict) or document.get('schema')!=_SCHEMA
                or document.get('requirements')!=_REQUIREMENTS
                or document.get('native_defaults')!=_DEFAULTS):
            raise ValueError('unsupported policy schema or state requirements')
        maps=document.get('audited_maps')
        actors=document.get('actors')
        if (not isinstance(maps,list) or not maps or
                any(type(name) is not str or not name for name in maps) or
                len(set(maps))!=len(maps) or not isinstance(actors,list)):
            raise ValueError('invalid policy map or actor inventory')
        result=set()
        for actor in actors:
            if not isinstance(actor,dict):raise ValueError('invalid policy actor')
            map_id=actor.get('map');vid=actor.get('vid');mask=actor.get('removetype')
            hp=actor.get('captured_hp');sources=actor.get('sources')
            if (map_id not in maps or type(vid) is not str or not vid
                    or type(mask) is not int or not 0<mask<2**31 or not mask&1
                    or not isinstance(hp,list) or not hp
                    or any(type(value) is not int or not 0<=value<2**31 for value in hp)
                    or actor.get('evidence') not in _EVIDENCE
                    or not isinstance(sources,list) or not sources
                    or any(type(source) is not str or not source for source in sources)):
                raise ValueError('invalid captured actor state')
            key=(map_id,vid,mask)
            if key in result:raise ValueError('duplicate captured actor state')
            result.add(key)
        return frozenset(result)
    except (OSError,UnicodeError,ValueError,TypeError,KeyError):
        logging.getLogger(__name__).warning('Native enemy death policy unavailable; local HP prediction disabled')
        return frozenset()

_POLICY=_load_policy()

_CAPTURED_RUN_POLICY=frozenset((
    ('efb_run','ice.bug',1),
    ('efb_run','ice.spiner',1),
    ('efb_run','ice.yetikong',1),
    ('efb_run','ice.tunneler',1),
    ('efb_run','ice.rdino',1),
    ('efb_run','ice.miteball',1),
    ('efb_run','ice.msworder',1),
    ('efb_run','ice.mcharger',1),
    ('efb_run','ice.mrangthrower',1),
    ('efb_run','ice.maxethrower',1),
    ('efb_run','ice.maxe',3),
))

def _field(props,name,default=_INVALID):
    'Accept wire and decoded-capture names; conflicting aliases fail shut.'
    wire=name+'_'
    if wire in props:
        if name in props and props[name]!=props[wire]:return _INVALID
        return props[wire]
    return props.get(name,default)

def _integer(value):
    if isinstance(value,Mapping):value=value.get('value',_INVALID)
    return value if type(value) is int else _INVALID

def native_hp_prediction(props,map_id,enabled=True):
    'Whether this currently damageable actor has runtime native HP removal.'
    if enabled is not True or type(map_id) is not str or not isinstance(props,Mapping):return False
    hp=_integer(_field(props,'hitpoints'))
    if hp is _INVALID or not 0<=hp<2**31:return False
    if _integer(_field(props,'ownertype',0))!=2:return False
    if _field(props,'invulnerable',False) is not False:return False
    if _field(props,'isinternal',False) is not False:return False
    if _integer(_field(props,'foreignclass',0))!=0:return False
    vid=_field(props,'vid')
    mask=_integer(_field(props,'removetype',1))
    if type(vid) is not str or type(mask) is not int or not 0<mask<2**31 or not mask&1:return False
    key=(map_id,vid,mask)

    return key in _POLICY or bool(_POLICY) and key in _CAPTURED_RUN_POLICY
