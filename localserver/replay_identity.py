"""Only the optional nickname explicitly supplied for a replay is shareable."""
import re

RESERVED = {'null', 'none', 'undefined', 'unknown', 'unknow', 'anonymous', 'nil', 'nan'}

def nickname(value):
    if value is None:return ''
    if not isinstance(value,str):raise ValueError('Nickname must use English letters and numbers')
    value=value.strip()
    if not value:return ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}',value):
        raise ValueError('Nickname: use up to 32 English letters, numbers, spaces, . _ or -')
    if re.sub('[^a-z0-9]','',value.lower()) in RESERVED:
        raise ValueError('This nickname is reserved')
    return value
