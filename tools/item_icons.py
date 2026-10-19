"""Original loot sprites, including composite implant icons, for battle history."""
import math
import struct

from localserver import pb_layout


def catalogue(manifest, items, asset, pack_name='base'):
    pack = next(p for p in manifest['pvid_'] if p['name_'] == pack_name)
    descriptor = asset('/texture/'+pack_name+'.' + pack['descid_'] + '.bpb')
    document = pb_layout.default_layouts().decode('ImageFileDescription', descriptor)
    images = []
    for index, sheet in enumerate(document['sheetdef_']):
        url = '/texture/'+pack_name+'.' + pack['imageids_'][index] + '.png'
        png = asset(url)
        if png[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Invalid original item sprite sheet')
        width, height = struct.unpack('>II', png[16:24])
        for rect in sheet['imagedef_']:
            images.append(dict(rect, sheet=url, sheet_width=width, sheet_height=height))
    frames = {row['vid_']: [dict(uiid_=row['uiid_'])] for row in document.get('imageviddesc_', [])}
    for row in document.get('animatedimagedesc_', []):
        for animation in row.get('animdesc_', []):
            sequence = animation.get('framedesc_', [])
            if sequence and sequence[0].get('element_'):
                frames[row['vid_']] = sequence[0]['element_']
                break
    result = {}
    for item in items.values():
        parts, corners = [], []
        for element in frames.get(item.get('lootvid_'), []):
            index = element.get('uiid_', -1)
            if not 0 <= index < len(images):
                continue
            rect = images[index]
            width, height = rect['w_'], rect['h_']
            if width <= 0 or height <= 0:
                continue
            ox, oy = element.get('offsetx_', 0), element.get('offsety_', 0)
            sx, sy = element.get('scalex_', 100) / 100, element.get('scaley_', 100) / 100
            angle = element.get('angledegt_', 0) / 10
            cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            px, py = rect.get('rpx_', 0), rect.get('rpy_', 0)
            for x, y in ((0, 0), (width, 0), (width, height), (0, height)):
                x, y = (x - px) * sx, (y - py) * sy
                corners.append((ox + x * cosine - y * sine, oy + x * sine + y * cosine))
            parts.append(dict(sheet=rect['sheet'], sheet_width=rect['sheet_width'], sheet_height=rect['sheet_height'],
                x=rect['x_'], y=rect['y_'], w=width, h=height, px=px, py=py,
                ox=ox, oy=oy, sx=sx, sy=sy, angle=angle, opacity=element.get('opacity_', 100) / 100))
        if parts:
            left, top = min(p[0] for p in corners), min(p[1] for p in corners)
            right, bottom = max(p[0] for p in corners), max(p[1] for p in corners)
            result[str(item['type_'])] = dict(viewbox=[left, top, max(1, right-left), max(1, bottom-top)], parts=parts)
    return result


def all_icons(manifest, items, asset):
    result=catalogue(manifest,items,asset)
    portraits={f'portrait-{shell}':dict(type_=f'portrait-{shell}',lootvid_='ui.port'+name)
              for shell,name in ((1,'wildfire'),(2,'duskwing'),(3,'ironclad'),(5,'fabricator'))}
    result.update(catalogue(manifest,portraits,asset,'ui'))
    return result
