"""Only the optional nickname explicitly supplied for a replay is shareable."""
import re
import unicodedata

RESERVED = {'null', 'none', 'undefined', 'unknown', 'unknow', 'anonymous', 'nil', 'nan'}
POLITICAL_NAMES = ('xijinping', 'maozedong', 'dengxiaoping', 'jiangzemin', 'hujintao',
    'likeqiang', 'liqiang', 'wenjiabao', 'zhurongji', 'zhouenlai',
    'donaldtrump', 'joebiden', 'vladimirputin', 'volodymyrzelensky')
POLITICAL_PHRASES = POLITICAL_NAMES + (
    '习近平', '習近平', '毛泽东', '毛澤東', '邓小平', '鄧小平', '江泽民', '江澤民',
    '胡锦涛', '胡錦濤', '李克强', '李克強', '李强', '李強', '温家宝', '溫家寶',
    '朱镕基', '朱鎔基', '周恩来', '周恩來', '共产党', '共產黨', '中国政治', '中國政治',
    '中共', '六四事件', '天安门事件', '天安門事件', '台独', '臺獨', '港独', '港獨',
    '藏独', '藏獨', '疆独', '疆獨', '台湾独立', '臺灣獨立', '香港独立', '香港獨立',
    '西藏独立', '西藏獨立', '新疆独立', '新疆獨立', '政治', '总统', '總統',
    'politics', 'political', 'communistparty', 'chinesecommunistparty',
    'taiwanindependence', 'freehongkong', 'freetibet',
    'дональдтрамп', 'владимирпутин', 'коммунистическаяпартия',
    '정치', '시진핑', '공산당', 'سياسة', 'الحزبالشيوعي')


def moderation_key(value):
    value=unicodedata.normalize('NFKC',value).casefold()
    return ''.join(c for c in value if c.isalnum())


def political_content(value, names_only=False):
    normalized=moderation_key(value)
    # Separate common ASCII substitutions from non-Latin text. This is a
    # bounded phrase filter, not a semantic classifier or an identity check.
    latin=normalized.translate(str.maketrans({'0':'o','1':'i','3':'e','4':'a','5':'s','7':'t'}))
    return any(term in normalized or term in latin for term in
               (POLITICAL_NAMES if names_only else POLITICAL_PHRASES))


def comment_text(value):
    if not isinstance(value,str):raise ValueError('Invalid comment')
    value=value.strip()
    if not 1<=len(value)<=1000 or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError('Use 1 to 1000 characters')
    if political_content(value):raise ValueError('political-content')
    return value

def nickname(value):
    if value is None:return ''
    if not isinstance(value,str):raise ValueError('Nickname must use English letters and numbers')
    value=value.strip()
    if not value:return ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}',value):
        raise ValueError('Nickname: use up to 32 English letters, numbers, spaces, . _ or -')
    if re.sub('[^a-z0-9]','',value.lower()) in RESERVED:
        raise ValueError('This nickname is reserved')
    if political_content(value,names_only=True):
        raise ValueError('political-nickname')
    return value


def sharing_code(value):
    if value is None or value=='':return ''
    if not isinstance(value,str) or len(value)>80:raise ValueError('Invalid original sharing code')
    value=re.sub(r'[-\s]','',value).upper()
    if not re.fullmatch(r'[A-HJKMNP-Z2-9]{12}',value):raise ValueError('Invalid original sharing code')
    return '-'.join(value[i:i+4] for i in range(0,12,4))
