"""Pure validated merge of a scoped native inventory into issued CreatePlayer.

Character items, account stash and EC change. A failed inventory read preserves
both old item partitions; complete items=[] clears both. No movement or packet
send occurs here, and no editor equipment-only normalizer is involved.
"""
import copy
import json
import math
from ... import pb_layout

SCHEMA='full-run-inventory-v1'
SCOPE_FIELDS=('map','room_token','room_seed','worldid','objid')

def _int(value,label,minimum=-2147483648,maximum=2147483647):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('invalid '+label)
    return value

def _message(typ,value,layouts,depth=0):
    if not isinstance(value,dict) or depth>6:raise ValueError('invalid '+typ)
    fields={f['name'].rstrip('_'):f for f in layouts.types[typ]['fields']};result={}
    for key,item in value.items():
        if not isinstance(key,str):raise ValueError('invalid '+typ+' field name')
        name=key.rstrip('_')
        if name not in fields or name+'_' in result:raise ValueError('unknown/duplicate '+typ+' field '+str(key))
        f=fields[name];kind=f['kind']
        if kind in ('int32','uint32'):item=_int(item,name,0 if kind=='uint32' else -2147483648,4294967295 if kind=='uint32' else 2147483647)
        elif kind=='bool':
            if type(item) is not bool:raise ValueError('invalid boolean '+name)
        elif kind=='stringPointer':
            if not isinstance(item,str) or len(item.encode('utf8'))>4096:raise ValueError('invalid string '+name)
        elif kind=='repeatedMessage':
            if not isinstance(item,list) or len(item)>128:raise ValueError('invalid repeated '+name)
            item=[_message(f['item'],v,layouts,depth+1) for v in item]
        elif kind=='messagePointer':item=_message(f['item'],item,layouts,depth+1)
        else:raise ValueError('unsupported ItemProperties field kind '+kind)
        result[name+'_']=copy.deepcopy(item)
    return result

def validate_items(snapshot,layouts=None,max_items=256,max_total_boosts=2048):
    layouts=layouts or pb_layout.default_layouts()
    if snapshot.get('items_ok') is not True:raise ValueError('native items read not successful')
    rows=snapshot.get('items');count=snapshot.get('count');quality=snapshot.get('quality') or {};audit=snapshot.get('slots')
    if not isinstance(rows,list) or type(count) is not int or not 0<=count<=max_items or len(rows)!=count:
        raise ValueError('incomplete native item count')
    if len(json.dumps(dict(items=rows,slots=audit),ensure_ascii=False,separators=(',',':')).encode('utf8'))>48000:
        raise ValueError('native item payload exceeds transport bound')
    if quality.get('stable') is not True or quality.get('slot_source')!='Item.slotPos' or quality.get('abi',{}).get('name')!='win32-gcc48-items-v1' or type(quality.get('declared_count')) is not int or type(quality.get('traversed_count')) is not int or quality.get('declared_count')!=count or quality.get('traversed_count')!=count:
        raise ValueError('native map/slot read is not complete and stable')
    if not isinstance(audit,list) or len(audit)!=count:raise ValueError('missing native slot observations')
    slots=set();keys=set();result=[];boosts=0
    for row,observed in zip(rows,audit):
        item=_message('ItemProperties',row,layouts)
        _int(item.get('type_'),'item type',1);slot=_int(item.get('slotpos_'),'item slot',1,65535)
        if slot in slots:raise ValueError('duplicate native item slot')
        if not isinstance(observed,dict) or observed.get('owner_ok') is not True or observed.get('native_slotpos')!=slot or observed.get('type')!=item['type_']:
            raise ValueError('item does not match actual native slot/owner/type')
        key=_int(observed.get('map_key'),'native map key')
        _int(observed.get('native_slotpos'),'observed native slot',1,65535);_int(observed.get('type'),'observed native type',1)
        if key in keys:raise ValueError('duplicate native item map key')
        valid_stats={v for k,v in layouts.enums['Stat'].items() if k.startswith('STAT_') and v!=0}
        if any(type(b.get('stat_')) is not int or b['stat_'] not in valid_stats for b in item.get('statboosts_',[])):
            raise ValueError('invalid/unknown native StatBoost')
        slots.add(slot);keys.add(key);boosts+=len(item.get('statboosts_',[]));result.append(item)
    if boosts>max_total_boosts:raise ValueError('total item affixes exceed bound')
    return result

def split_native_items(items):
    """Mirror PlayerCharacter::updateProps, 0x418794..0x4187a1 (retail Win32).

    Stash is the full 0x1xxx range, not only enum SLOT_STASH's 0x1000 high byte.
    Preserve every other observed slot in charprops, as the native serializer
    does. This partitions a validated snapshot; it never filters items or
    invents capacities/unlocks. Item.slotPos already supplies the current slot.
    """
    character=[];stash=[]
    for item in items:
        slot=_int(item.get('slotpos_'),'item slot',1,65535)
        (stash if slot&0xf000==0x1000 else character).append(copy.deepcopy(item))
    return character,stash

def merge_player_inventory(create_player,snapshot,*,now,expected_scope,max_age_s=.75,expected_request_id=None,layouts=None):
    """Return (deepcopy CreatePlayer, per-component result); never mutates input.

    expected_scope must come from the departing session BEFORE run.move.
    room_seed is the old recipe's actual worldprops.seed_, not its RNG seed.
    For a door refresh handshake, require the nonce issued AFTER the door intent
    with expected_request_id. A merely recent unsolicited sample is insufficient.
    """
    result=copy.deepcopy(create_player);report=dict(items_applied=False,stash_applied=False,ec_applied=False,ready_for_handoff=False,errors=[])
    try:
        if not isinstance(snapshot,dict) or snapshot.get('schema')!=SCHEMA:raise ValueError('missing/unsupported native inventory snapshot')
        if not isinstance(result,dict):raise ValueError('invalid issued CreatePlayer')
        if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('invalid merge time')
        if type(max_age_s) not in (int,float) or not math.isfinite(max_age_s) or max_age_s<0:raise ValueError('invalid inventory freshness bound')
        sampled=snapshot.get('sampled_at')
        if type(sampled) not in (int,float) or not math.isfinite(sampled) or not -.25<=now-sampled<=max_age_s:
            raise ValueError('native inventory snapshot is stale/future')
        scope=snapshot.get('scope') or {}
        if not isinstance(scope,dict) or not isinstance(expected_scope,dict) or any(expected_scope.get(k) is None or scope.get(k)!=expected_scope[k] for k in SCOPE_FIELDS):
            raise ValueError('native inventory room/player scope mismatch')
        if expected_request_id is not None:
            _int(expected_request_id,'expected inventory request ID',1,9223372036854775807)
            if type(snapshot.get('request_id')) is not int or snapshot['request_id']!=expected_request_id:
                raise ValueError('native inventory refresh request mismatch')
        if not isinstance(scope.get('map'),str) or not scope['map'] or any(type(scope.get(k)) is not int for k in SCOPE_FIELDS if k!='map'):
            raise ValueError('invalid native inventory scope types')
        for k in ('worldid','objid','room_token'):
            if scope[k]<=0:raise ValueError('invalid native inventory scope identity')
        character=result.get('charprops_')
        if not isinstance(character,dict):raise ValueError('issued CreatePlayer lacks charprops')
        if 'accountprops_' in result and not isinstance(result['accountprops_'],dict):raise ValueError('invalid issued accountprops')

        if scope.get('charid')!=character.get('charid_') or scope.get('player_type')!=character.get('type_'):
            raise ValueError('native inventory character identity mismatch')
        _int(scope.get('charid'),'native character ID',0,4294967295);_int(scope.get('player_type'),'native character type',0)
    except (ValueError,TypeError) as error:
        report['errors'].append(str(error));return result,report
    try:
        items=validate_items(snapshot,layouts);character_items,stash=split_native_items(items)
        account=result.get('accountprops_',{});progress=account.get('progress_',{})
        if not isinstance(progress,dict):raise ValueError('invalid issued account progress')

        result['charprops_']['items_']=character_items
        result.setdefault('accountprops_',{}).setdefault('progress_',{})['stash_']=stash
        report.update(items_applied=True,stash_applied=True,item_count=len(items),
            character_item_count=len(character_items),stash_item_count=len(stash))
    except (ValueError,TypeError,UnicodeError,AttributeError) as error:report['errors'].append('items: '+str(error))
    try:
        if snapshot.get('ec_ok') is not True:raise ValueError('native EC read not successful')
        ec=_int(snapshot.get('ec'),'native EC',0)
        result.setdefault('accountprops_',{})['ec_']=ec;report['ec_applied']=True
    except (ValueError,TypeError) as error:report['errors'].append('ec: '+str(error))
    report['ready_for_handoff']=report['items_applied'] and report['stash_applied'] and report['ec_applied']
    return result,report
