"""Sprite indices are pack-wide; composite icons keep every original layer."""
import struct
import unittest

from localserver import pb_layout
from tools.item_icons import catalogue


class ItemIconTests(unittest.TestCase):
    def test_cross_sheet_indices_composite_layers_and_missing_icons(self):
        manifest = dict(pvid_=[dict(name_='base', descid_='d.test', imageids_=['0.test', '1.test'])])
        descriptor = dict(sheetdef_=[dict(imagedef_=[dict(x_=1,y_=2,w_=12,h_=18,rpx_=6,rpy_=9)]),
                                     dict(imagedef_=[dict(x_=31,y_=42,w_=20,h_=24,rpx_=10,rpy_=12)])],
            animatedimagedesc_=[dict(vid_='loot-composite', animdesc_=[dict(length_=66,
                framedesc_=[dict(element_=[dict(uiid_=0),dict(uiid_=1,offsetx_=5,offsety_=-3,scalex_=120,opacity_=86)])])])])
        encoded = pb_layout.default_layouts().encode('ImageFileDescription', descriptor)
        png = b'\x89PNG\r\n\x1a\n' + bytes(8) + struct.pack('>II',2048,2048)
        files = {'/texture/base.d.test.bpb':encoded,'/texture/base.0.test.png':png,'/texture/base.1.test.png':png}
        icons = catalogue(manifest,{1:dict(type_=1,lootvid_='loot-composite'),2:dict(type_=2,lootvid_='missing')},files.__getitem__)
        self.assertEqual(set(icons),{'1'})
        parts=icons['1']['parts']
        self.assertEqual([p['sheet'] for p in parts],['/texture/base.0.test.png','/texture/base.1.test.png'])
        self.assertEqual([p['x'] for p in parts],[1,31])
        self.assertEqual(parts[1]['sx'],1.2)
        self.assertEqual(parts[1]['opacity'],.86)
        self.assertTrue(all(size>0 for size in icons['1']['viewbox'][2:]))

    def test_invalid_sprite_sheet_is_rejected(self):
        manifest=dict(pvid_=[dict(name_='base',descid_='d.test',imageids_=['0.test'])])
        descriptor=pb_layout.default_layouts().encode('ImageFileDescription',dict(sheetdef_=[dict(imagedef_=[])]))
        with self.assertRaisesRegex(ValueError,'sprite sheet'):
            catalogue(manifest,{},lambda url:descriptor if url.endswith('.bpb') else b'not a PNG')


if __name__=='__main__':unittest.main()
