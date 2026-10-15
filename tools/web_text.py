"""Display names for the browser shell; original game item names are retained."""
MAPS_ZH = {
    'lobby': '终焉之地', 'home': '家园',
    'ice_mid': '精英冰封荒原 · 中途首领', 'ice_boss': '精英冰封荒原 · 首领',
    'forest_mid': '精英火焰森林 · 中途首领', 'forest_boss': '精英火焰森林 · 首领',
    'city_mid': '精英失落之城 · 中途首领', 'city_boss': '精英失落之城 · 首领',
    'cave_boss': '精英菌菇洞穴 · 首领',
    'dumb_normal_wave': '精英地下基地 · 波次', 'dumb_mid': '精英地下基地 · 中途首领',
    'dumb_wave': '精英地下基地 · 无限波次', 'dumb_boss': '精英地下基地 · 首领',
    'jungle_mid': '精英丝巢丛林 · 中途首领', 'jungle_boss': '精英丝巢丛林 · 首领',
    'hulk_normal': '墓地 · 首领', 'hulk_mid': '精英墓地 · 中途首领', 'egy_hulk': '精英墓地 · 首领',
    'lab_mid': '精英失落神庙 · 中途首领', 'lab_hard': '精英失落神庙 · 首领',
    'sc_patriarch': '星之大教堂 · 族长', 'sc_hive': '星之大教堂 · 蜂巢',
    'sc_bonesaw': '星之大教堂 · 骨锯', 'sc_boss': '星之大教堂 · 首领',
    'sc_boss_elite': '精英星之大教堂 · 首领', 'egy_run': '精英墓地 · 整关',
    'eub_run': '精英地下基地 · 整关', 'etl_run': '精英失落神庙 · 整关',
    'est_run': '精英丝巢丛林 · 整关', 'efb_run': '精英冰封荒原 · 整关', 'eff_run': '精英火焰森林 · 整关',
}


def map_names(map_id, title):
    return dict(zh=MAPS_ZH.get(map_id, title), en=title.replace(' · 整关试玩', ' · Full run'))
