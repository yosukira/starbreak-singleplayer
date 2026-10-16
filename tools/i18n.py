"""English/Chinese editor labels and local language preferences."""
import ctypes
import json
import locale
import os
from pathlib import Path

PREFERENCE = Path(__file__).resolve().parents[1] / 'userdata' / 'ui.json'
LANGUAGES = {'auto': 'System / 跟随系统', 'zh': '简体中文', 'en': 'English'}

EN = {
    '检查更新': 'Check for updates',
    '按键设置': 'Keyboard settings', '互动键': 'Interaction key',
    '显示/隐藏编辑器': 'Show / hide editor', '备用编辑器快捷键': 'Alternate editor shortcut',
    '重开当前局': 'Restart current run', '切换 WF': 'Switch to WF', '切换 DW': 'Switch to DW',
    '切换 FAB': 'Switch to FAB', '切换 IC': 'Switch to IC',
    '长按时间（秒）': 'Hold duration (seconds)', '恢复默认按键': 'Reset keys', '关闭': 'Close',
    '互动键默认跟随游戏绑定；识别不正确时，选择与游戏设置相同的键。':
        'Interaction follows the game binding. If detection fails, select the same key as in the game settings.',
    'Auto = 跟随游戏；None = 关闭快捷键。移动和攻击等操作使用游戏内的按键设置。':
        'Auto = follow game; None = disable shortcut. Movement and attack bindings use the game settings.',
    '按键不能重复；保留至少一个编辑器快捷键，长按时间为 0.5–5 秒。':
        'Use distinct keys, keep at least one editor shortcut, and set hold duration to 0.5–5 seconds.',
    '按键已保存，立即生效。': 'Keyboard settings saved and applied.', '隐藏': 'Hide',
    '编辑器快捷键为全局按键，建议使用 F 键。': 'Editor shortcuts are global; function keys are recommended.',
    '保存后自动重开应用。内容可滚动，Shift + 滚轮可左右滚动。':
        'Saving restarts the run. Scroll for more controls; Shift + wheel scrolls horizontally.',
    '显示/隐藏：{0} / {1}；长按 {2} 秒：重开 {3}，WF {4}，DW {5}，FAB {6}，IC {7}':
        'Show/hide: {0} / {1}; hold {2}s: restart {3}, WF {4}, DW {5}, FAB {6}, IC {7}',
    'StarBreak 装备编辑': 'StarBreak Gear Editor',
    '  ` / F8 显示/隐藏    |    长按 R 重开当前局，长按 7/8/9/0 换角色（WF/DW/FAB/IC）':
        '` / F8: show/hide  |  Hold R: restart  |  Hold 7/8/9/0: WF/DW/FAB/IC',
    '职业': 'Class', '角色 MK': 'Class MK', '植入体槽': 'Implant slots',
    '地图': 'Map', '血包 %': 'Health %', '弹药 %': 'Ammo %',
    '保存设置': 'Save settings', '（当前地图，各自保存）': '(Saved per map)',
    '敌人血量': 'Enemy HP', '敌人伤害': 'Enemy damage', '敌人速度': 'Enemy speed',
    '刷怪速率': 'Spawn rate', '刷怪上限': 'Spawn limit',
    '（0.1×–100×，可直接输入；1×=原版；数值对新刷出的怪/弹生效，上限=房间同时存活地面怪，飞机不计）':
        '0.1×–100×; type exact values. 1× = default. Applies to new enemies/shots. Limit counts ground enemies.',
    '炮台数量': 'Turrets', '前面': 'Front', '上面': 'Upper', '下面': 'Lower',
    '上追踪': 'Upper seeking', '下追踪': 'Lower seeking', '随机': 'Random',
    '（egy / hulk_normal 可用）': '(Graveyard Boss maps only)',
    '搜索物品名称或 ID': 'Search item name or ID',
    '显示所有职业装备': 'Show equipment for all classes',
    '装备排序：ID': 'Equipment sort: ID', '装备排序：首字母': 'Equipment sort: A–Z',
    '槽位': 'Slots', '主武器': 'Main weapon', '副武器': 'Secondary weapon',
    '特殊武器': 'Special weapon', '衣服': 'Armor', '机动/鞋': 'Mobility',
    '植入体': 'Implant', '背包 {0}': 'Inventory {0}',
    '物品 type / 名称': 'Item ID / name', '基础属性：—': 'Base stats: —',
    '基础属性：{0}': 'Base stats: {0}',
    '实例词缀（最终值 = 描述基础值 + 这里）': 'Item modifiers (final value = base + modifier)',
    '词缀顺序：本武器实测优先；其余按 ID 或首字母排序':
        'Item-specific modifiers first; sort others by ID or name.',
    '优先显示本物品词缀（Wiki / CHM）：{0}': 'Suggested modifiers: {0}',
    '本物品词缀默认值：{0}': 'Modifier defaults for this item: {0}',
    '本物品暂无 Wiki / CHM 词缀池；词缀按 ID 或首字母排序。':
        'No suggested modifier pool for this item; sort by ID or name.',
    '应用/保存': 'Apply / Save', '导出': 'Export', '导入': 'Import',
    '重新载入': 'Reload', '词缀排序：ID': 'Sort: ID', '词缀排序：首字母': 'Sort: name',
    '退出': 'Exit', '隐藏 (~/F8)': 'Hide (~/F8)', '+ 添加词缀': '+ Add modifier',
    '删除': 'Remove',
    '保存后自动重开应用；长按 R=重开，长按 7/8/9/0=WF/DW/FAB/IC':
        'Saving restarts the run. Hold R: restart; hold 7/8/9/0: WF/DW/FAB/IC.',
    '掉落率请填 0-100 的数字': 'Enter drop percentages between 0 and 100.',
    '数值倍率/刷怪上限请填数字': 'Enter numeric multipliers and a numeric spawn limit.',
    '已保存设置：地图立即重载，掉落率立即生效，数值对新刷出的怪生效':
        'Settings saved. Map reloads now; drop rates apply now; balance applies to new enemies.',
    '找不到 class_loadouts.json，只保存了 overrides': 'Default loadouts were not found; only overrides were saved.',
    '已保存 overrides；class_loadouts.json 不存在': 'Overrides saved; default loadouts are missing.',
    '已保存 MK / 炮台数量 / 装备；服务器检测到文件变化后会自动重开本局':
        'MK, turrets and equipment saved. The server will restart this run.',
    '导出装备配置': 'Export loadout', '导入装备配置': 'Import loadout',
    '已导出: ': 'Exported: ', '已导入；点“应用/保存”写装备与设置':
        'Imported. Click Apply / Save to save equipment and settings.',
    '语言': 'Language', 'Gear Editor': 'Gear Editor',
    '子弹基础速度': 'Base projectile speed',
    '此物品没有可编辑的弹体基础速度。': 'This item has no editable base projectile speed.',
    '{0}  （原值 {1}）': '{0}  (default {1})',
    '恢复原值': 'Reset to default',
    '恢复默认配置': 'Restore defaults',
    '追踪弹延迟 (ms)': 'Homing missile delay (ms)',
    '首领与小怪共用；瞄准玩家多少毫秒前的位置；0 = 当前位置，默认 275ms':
        'Shared by boss and enemy missiles; aim at the player’s position this many milliseconds ago; 0 = current position, default 275ms',
    '追踪弹延迟请填 0–500 的整数': 'Enter a homing missile delay from 0 to 500 milliseconds (whole numbers).',
    '跳过开局': 'Skip opening',
    '新一局生效；直接从首领飞船停稳后开始': 'Applies to the next attempt; start with the boss ship in position',
    '全部配装与战斗设置已恢复默认；原配置已备份：': 'All loadouts and encounter settings restored; previous settings backed up: ',
    '整数 0–1000000；同名武器共用。保存后退出并重新启动模拟器生效。':
        'Integer 0–1000000; shared by all instances of this item. Save, exit the game, then relaunch.',
    '弹速必须是 0–1000000 的整数': 'Projectile speed must be an integer between 0 and 1000000.',
    '子弹速度请填 0–1000000 的整数': 'Enter an integer projectile speed between 0 and 1000000.',
    '弹速已保存；请退出游戏并重新启动模拟器，长按 R 不会更新基础弹速。':
        'Projectile speed saved. Exit the game and relaunch the simulator; holding R does not reload base speeds.',
    '弹速配置必须是对象': 'Projectile speed settings must be an object.',
    '弹速配置中的物品 ID 或弹体配置无效': 'Invalid item ID or projectile configuration.',
    '弹速必须是 0–1000000 的整数，弹体索引必须非负':
        'Projectile speeds must be integers from 0 to 1000000; projectile indices must be non-negative.',
}
ZH = {
    'Gear Editor': '装备编辑器',
    '优先显示本物品词缀（Wiki / CHM）：{0}': '建议词缀：{0}',
    '本物品暂无 Wiki / CHM 词缀池；词缀按 ID 或首字母排序。': '此物品暂无建议词缀；按 ID 或名称排序。',
    '词缀顺序：本武器实测优先；其余按 ID 或首字母排序': '本物品建议词缀优先；其余按 ID 或名称排序',
}
def system_language():
    """Use the Windows UI language, then locale settings; default to English."""
    if os.name == 'nt':
        try:
            # Primary language 0x04 covers all Chinese regional UI locales.
            return 'zh' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff == 4 else 'en'
        except (AttributeError, OSError):
            pass
    candidates=[os.environ.get(k,'') for k in ('LC_ALL','LC_MESSAGES','LANG')]
    try:candidates.append(locale.getlocale()[0] or '')
    except (ValueError,TypeError):pass
    return 'zh' if next((v for v in candidates if v), '').lower().startswith('zh') else 'en'

def load_preference(path=PREFERENCE):
    try:
        value=json.loads(Path(path).read_text(encoding='utf8')).get('language','auto')
    except (OSError,ValueError,AttributeError):value='auto'
    return value if value in LANGUAGES else 'auto'

def save_preference(value,path=PREFERENCE):
    if value not in LANGUAGES:raise ValueError('Unsupported language')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps({'language':value})+'\n',encoding='utf8')
    temp.replace(path)

def resolve_language(preference):
    return system_language() if preference=='auto' else preference if preference in ('zh','en') else 'en'

def translate(text,language):
    if language=='zh':return ZH.get(text,text)
    if text.startswith('背包 '):return 'Inventory '+text.split(' ',1)[1]
    if text.startswith('物品 ') and '没有可编辑的基础速度' in text:
        import re
        values=re.findall(r'\d+',text)
        if len(values)==2:return 'Item {0} projectile {1} has no editable base speed.'.format(*values)
    return EN.get(text,text)

def map_title(map_id,title,language):
    """Keep the map's original display name in every interface language."""
    return title
