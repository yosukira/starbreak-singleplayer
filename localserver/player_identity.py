"""Local display identity and private in-game victory announcements."""
import json
import os
from pathlib import Path
from . import paths
from .replay_identity import nickname


def normalize(value):
    name=nickname(value.get('character_name',''))
    guild=value.get('guild_name','')
    if not isinstance(guild,str) or len(guild.strip())>32 or any(ord(c)<32 for c in guild):raise ValueError('Guild name: use up to 32 characters')
    return dict(character_name=name,guild_name=guild.strip(),callouts=value.get('callouts',True) is not False)


def load():
    path=Path(os.environ.get('SB_PLAYER_IDENTITY',str(paths.USERDATA/'identity.json')))
    try:return normalize(json.loads(path.read_text(encoding='utf8')))
    except (OSError,ValueError,TypeError):return normalize({})


def apply(player):
    identity=load();name='username_' if 'username_' in player else 'username'
    player[name]=identity['character_name'] or 'Localplayer'
    player.pop('factionname',None);player.pop('factionname_',None)
    if identity['guild_name']:player['factionname_' if name.endswith('_') else 'factionname']=identity['guild_name']
    return player


def callout(title,identity=None):
    identity=load() if identity is None else normalize(identity)
    actor=identity['character_name'] or 'Localplayer'
    if identity['guild_name']:actor+=' of '+identity['guild_name']
    return 'The '+title.removesuffix(' Boss')+' has been defeated! '+actor+' gave an impressive performance!'


def announce(session,title):
    identity=load()
    if not identity['callouts']:return
    server=getattr(session,'server',None);channel=getattr(session,'channel',None)
    if server is None or channel is None:return
    text=callout(title,identity)
    payload=server.layouts.encode('Chat',dict(message_=text,filteredmessage_=text,chattype_=3))
    server._send_raw(channel,20,payload,'victory-callout',record=False)
