# StarBreak Singleplayer

[中文](#中文使用说明) · [English](#english-usage)

StarBreak 的本地单人战斗模拟器 / A local singleplayer battle simulator for StarBreak.

**网页版 / Browser edition:** [使用说明 / Instructions](web/README.md).
Windows 和 Mac 可以运行本地浏览器战斗；玩家包自带 Python，无需 SBPE 或另装运行环境。
Windows and Mac browser player packages include Python and run locally without SBPE.
首次需联网下载原版资源 / Internet access is needed for the initial original-game assets.

## 中文使用说明

### 功能与运行环境

- 多个区域的 Mid、Boss、波次挑战和整关试玩，可在装备编辑器里选择。
- 四职业切换、MK、装备、词缀、植入体槽、补给掉率，以及部分地图的炮台和数值设置。
- 装备编辑器支持简体中文、English 和跟随系统；切换语言会保留当前未保存的编辑。
- **运行游戏需要 Windows、StarBreak Windows 客户端和兼容的 [SBPE](https://github.com/atomizer/sbpe) 安装。** 客户端补丁针对 StarBreak 1.14.6 的 Windows 二进制；其他版本可能不兼容。
- **Windows 玩家 Release 已自带 Python 和 Tk，无需另外安装 Python 或配置 PATH。** 游戏内注入仍使用 SBPE 自带的 32 位 PyPy。GitHub 的 Source code / Download ZIP 是源码包，不包含这套运行环境。
- Mac 可使用[浏览器战斗版](web/README.md)。下文 `start.cmd` 专用于 Windows 原生版。

### 安装与启动

1. 先安装并配置好 SBPE，确认 SBPE 能正常启动你的 StarBreak。SBPE 的 `config.ini` 应指向自己的游戏客户端。
2. 从 [Releases](https://github.com/yosukira/starbreak-singleplayer/releases/latest) 下载 **`starbreak-singleplayer-windows-*.zip` 玩家包**，不要选 Source code。
3. 完整解压，**将整个 `starbreak-singleplayer` 文件夹放进 SBPE 安装目录**，保留内含的 `python` 文件夹。项目文件夹可以重命名，但内部结构须保留。
4. 双击项目文件夹里的 **`start.cmd`**。装备编辑器和游戏会分别打开；关闭游戏后本场本地服退出。无需安装 Python。

目录应类似：

```text
SBPE/
├── config.ini
├── loader.py
├── symbols.json
├── build/remote.bin
├── pypy/pypy3.exe
├── plugins/
└── starbreak-singleplayer/
    ├── start.cmd
    ├── python/            # 玩家包自带的运行环境 / bundled player runtime
    ├── tools/
    ├── localserver/
    ├── maps/
    └── data/
```

启动器会生成供本地连接使用的客户端副本，保留原客户端文件。`start.cmd` 使用上一级 SBPE 目录的插件和配置，并安装本模拟器的运行插件；SBPE 配置在正常退出时恢复。请先关闭其他使用同一 SBPE 的游戏进程。

也可以在 Windows 终端手动启动：

```powershell
.\python\python.exe tools/play_hulk.py --sbpe ".." --sbpe-direct
```

如果游戏路径没有从 SBPE 配置识别出来，可显式添加：

```powershell
.\python\python.exe tools/play_hulk.py --sbpe ".." --sbpe-direct --game "C:\Games\StarBreak"
```

### 使用装备编辑器

1. 选择地图、职业、MK 和植入体槽数。
2. 在左侧选择槽位，在右侧选择物品并编辑词缀。物品和关卡名称保留原来的名字；界面和属性标签随语言切换。
3. 点击 **应用/保存**。服务器检测到配装变化后会重开当前局。地图、掉落率等设置通过 **保存设置** 保存。
4. 右上角选择 **System / 跟随系统**、**简体中文** 或 **English**。首次默认跟随系统：Windows 中文界面使用中文，其他语言使用英文；选择会在本机保存。
5. **导出 / 导入** 用于分享 JSON 配装；导入后点击应用/保存才会写入配装。
6. **恢复默认配置** 立即重置四职业配装、弹速与战斗设置，原配置先备份到 `userdata/before-restore-defaults-*`；语言与按键不变。

编辑器适配 1366×768 等低分辨率屏幕：内容区支持滚动，底部操作按钮固定显示；按住 Shift 滚动可左右查看较宽的控件。

GY（Graveyard Boss）与 EGY（Elite Graveyard Boss）的 **跳过开局** 开关默认关闭，两图分别保存。开启并保存后，新一局直接从首领飞船停稳、平台就绪的阶段开始，跳过大机器人与飞船进场。恢复默认配置会关闭此开关。

**追踪弹延迟 (ms)** 在 GY / EGY 分别保存，范围 0–500ms，默认 275ms。Boss 与小怪的追踪弹共用此设置。它决定转向时瞄准玩家多久以前的位置，0ms 表示当前位置；不改变 50ms 的转向更新间隔或发射 CD。保存后生效，恢复默认配置会将两图都恢复到 275ms。

点击右上角 **按键设置**，可以修改编辑器显示/隐藏、重开、四职业切换的快捷键和长按时间。互动键默认为 **Auto**，跟随游戏当前绑定；识别异常时，手动选择与游戏设置相同的键。移动和攻击等操作在游戏内设置。快捷键保存后生效，并保存在本机 `userdata/keybindings.json`。

选择有明确弹速参数的武器后，可在 **子弹基础速度** 中修改每种弹体的基础速度（整数 `0–1000000`）。同一物品的全部实例共用此设置；原有速度词缀继续保留，存活时间不变，因此射程也可能改变。数值沿用原版武器参数，不统一标作 px/s；`0` 是零基础速度，不是恢复默认。点 **恢复原值** 可还原。

**修改弹速后点击应用/保存，再退出游戏并重新运行 `start.cmd`；长按 R 不会重新加载基础弹速。** 弹速设置会随配装导入/导出，不会改写原始物品目录。

| 操作 | 按键 |
| --- | --- |
| 显示 / 隐藏编辑器（Windows） | `~` 所在键或 `F8` |
| 重开当前局 | 长按 `R` |
| 切职业 WF / DW / FAB / IC | 分别长按 `7` / `8` / `9` / `0` |

Mac 编辑器预览：安装带 Tk 的 Python 3.10+ 后，在项目目录运行 `python3 tools/gear_editor.py --show`，或运行 `preview-editor.command`。Mac 预览的保存会写入本机配置，Windows 全局热键及游戏效果不在预览范围内。

### 本地数据与常见问题

- 首次启动自动创建 `userdata/`，保存自己的配装、语言偏好和日志；可在编辑器点击“恢复默认配置”。它不会进入 Git 版本控制。
- 初始四职业配装恢复为本地练习版的完整预设，包含自定义词缀和数值。这些数值用于练习，不是原服推荐配装或原服词缀范围说明。
- 从旧版更新时，仅自动替换仍与旧版错误占位预设完全一致的职业配装，并补齐缺失职业；已经修改的职业配装保留。被替换的文件先备份为 `userdata/*.before-preset-repair.json`。WF Aeonic 默认爆伤已修正为 0.5×；IC Corrupt Sledge 默认 Speed% 为 20，去掉原先三条额外伤害/物品暴击词缀。仅自动升级仍完全匹配旧默认的物品，自定义词缀保留并可用“恢复默认配置”主动重置。仅当四职业都未改动且词缀覆盖文件仍为空时，才自动恢复整份默认词缀覆盖。
- 不需要原服账号登录。本地玩家采用固定的虚拟身份，原服账号进度与背包不会导入。
- 提示缺少 `remote.bin`、`symbols.json` 或 `pypy3.exe`：检查项目是否放在完整的 SBPE 安装目录下。
- 提示 Python 未找到或运行环境检查失败：重新完整解压 Windows 玩家包，确认 `start.cmd` 旁有 `python/python.exe`。SBPE 的旧版 PyPy 不能代替本地服和编辑器所需的 Python。
- 编辑器没有打开：确认玩家包的 `python/tcl`、`python/tkinter` 也已解压，并查看 `userdata/logs/` 中的日志。
- 只下载源码或在 Mac 预览：自行准备带 Tk 的 Python 3.10+；Windows 源码启动器也可识别系统 Python 或 SBPE 目录下的 `python/python.exe`。
- 补丁提示版本或字节不匹配：当前客户端二进制不符合补丁预期，不要用不匹配的补丁强行覆盖。
- 地图和机制为模拟实现，部分行为及数值可能与原服不同。整关试玩不代表全部原版生成规则已经恢复。

### Discord channel
https://discord.gg/J9YvUCuGmv

### 许可

模拟器代码采用 [MIT](LICENSE)。Windows 玩家包附带的 Python 和 Tcl/Tk 保留各自许可，见 `python/LICENSE.txt`、`python/TCL-LICENSE.txt` 和 `python/TK-LICENSE.txt`。StarBreak 游戏客户端、美术、音频及 SBPE 不包含在本仓库中，其权利与许可由各自所有者决定；运行时需要用户自己的客户端及 SBPE 安装。

## English usage

### Features and requirements

- Practice Mid encounters, Boss fights, wave challenges and full-run simulations from several zones.
- Switch between all four shells; edit MK, equipment, modifiers, implant slots and supply drop rates. Some maps also expose turret and balance settings.
- The separate gear editor supports **English**, **简体中文** and **System / 跟随系统**, preserving unsaved edits when switching languages.
- **Playing the game requires Windows, the Windows StarBreak client and a compatible [SBPE](https://github.com/atomizer/sbpe) installation.** Client patches target the StarBreak 1.14.6 Windows binary. Other versions may be incompatible.
- **The Windows player Release includes Python and Tk. No separate Python installation or PATH setup is needed.** SBPE's own 32-bit PyPy remains necessary for injection. GitHub's Source code / Download ZIP contains source only, without this runtime.
- macOS can preview the gear editor independently. This package does not provide a macOS game launcher or native injection backend.

### Install and launch

1. Install and configure SBPE first. Make sure it can launch your StarBreak client. Its `config.ini` should point to your own game installation.
2. Download the **`starbreak-singleplayer-windows-*.zip` player package** from [Releases](https://github.com/yosukira/starbreak-singleplayer/releases/latest). Do not choose Source code.
3. Extract the complete archive and **place the entire `starbreak-singleplayer` folder inside your SBPE installation directory**, as shown above. Keep the included `python` folder. You may rename the project folder while preserving its contents.
4. Double-click **`start.cmd` inside the project folder**. The editor and game open separately. Closing the game stops that session's local server. You do not need to install Python.

The launcher creates a separate client copy for local connections and retains the original client file. It uses the parent SBPE directory's plugins and configuration, installs the simulator's runtime plugins and restores SBPE configuration on normal exit. Close other game processes using the same SBPE installation before launching.

From a Windows terminal in the project folder:

```powershell
.\python\python.exe tools/play_hulk.py --sbpe ".." --sbpe-direct
```

If the game path cannot be detected from SBPE configuration:

```powershell
.\python\python.exe tools/play_hulk.py --sbpe ".." --sbpe-direct --game "C:\Games\StarBreak"
```

### Gear editor

1. Select a map, shell, MK and implant slot count.
2. Select an equipment slot on the left, then choose an item and edit its modifiers on the right. Item and map names retain their original names; UI labels and stat labels follow the selected language.
3. Click **Apply / Save**. The server restarts the current run when it detects the new loadout. Use **Save settings** for map selection, drop rates and other map settings.
4. Select a language in the top-right corner. The first launch follows your system language: Chinese Windows UI locales use Chinese; other locales use English. Your selection is saved locally.
5. **Export / Import** shares JSON loadouts. After importing, click Apply / Save to write the equipment changes.

The editor fits low-resolution screens such as 1366×768. Its contents scroll while the bottom action buttons remain visible; hold Shift while scrolling to view wide controls horizontally.

Use **Keyboard settings** in the top-right corner to change editor shortcuts, restart and class-switch keys, and hold duration. Interaction defaults to **Auto**, following the game's current binding. If detection fails, select the same key as in the game settings. Movement and attack bindings use the game settings. Shortcut changes apply after saving and are stored locally in `userdata/keybindings.json`.

Weapons with an explicit speed parameter expose **Base projectile speed** for each projectile. Enter an integer from `0` to `1000000`. The setting applies to every instance of that item; existing speed modifiers remain, and lifetime is unchanged, so range may change. Values use the original weapon parameters and are not universally pixels per second. `0` means zero base speed, not the default. Use **Reset to default** to restore the original value.

**After saving a projectile speed change, exit the game and relaunch `start.cmd`; holding R does not reload base speeds.** Speed settings are included in loadout import/export and never rewrite the original item catalogue.

| Action | Key |
| --- | --- |
| Show / hide the editor (Windows) | The key left of `1` or `F8` |
| Restart the run | Hold `R` |
| Switch to WF / DW / FAB / IC | Hold `7` / `8` / `9` / `0`, respectively |

To preview the editor on macOS, install Python 3.10+ with Tk and run `python3 tools/gear_editor.py --show` from the project folder, or run `preview-editor.command`. Saving in this preview writes local settings; Windows global hotkeys and in-game effects cannot be verified there.

**Restore defaults** immediately resets all four class loadouts, projectile speeds and encounter settings to the shipped presets. Previous files are backed up under `userdata/before-restore-defaults-*`; language and key bindings are retained.

For GY / EGY Boss, **Skip opening** defaults to off and is saved per encounter. Enable it and save to begin the next attempt with the boss ship in position and platforms ready, skipping the opening robots and ship arrival. Restore defaults turns it off.

**Homing missile delay (ms)** is saved separately for GY / EGY Boss: 0–500ms, default 275ms. Boss missiles and enemy homing missiles share this setting. It changes how old the player position used for steering is, not the 50ms steering interval or firing cooldown. 0ms uses the current position. Save to apply; Restore defaults returns both maps to 275ms.

### Local files and troubleshooting

- First launch creates `userdata/` for your own loadouts, language preference and logs. Use **Restore defaults** in the editor to reset equipment and encounter settings while keeping a backup. It is excluded from Git.
- The initial loadouts for all four shells restore the complete local practice presets, including custom modifiers and values. These are practice settings, not recommended retail loadouts or a specification of retail modifier ranges.
- When updating, automatic repair replaces only shell loadouts that still exactly match the old incorrect placeholder and fills missing shells. Edited shell loadouts are retained. Replaced files are backed up as `userdata/*.before-preset-repair.json`. The default modifier overrides are restored automatically only when all four shells are unchanged and the override file is still empty. WF Aeonic default crit damage is now 0.5×; IC Corrupt Sledge defaults to Speed% 20 without the old flat damage/item crit damage/item crit chance extras. Automatic upgrades touch only exact old item presets; edited modifiers are preserved.
- No original-server account login is required. A synthetic local player is used; online account progress and inventory are not imported.
- Missing `remote.bin`, `symbols.json` or `pypy3.exe`: check the project folder is inside a complete SBPE installation.
- Python missing or runtime check failed: re-extract the complete Windows player ZIP and make sure `python/python.exe` is next to `start.cmd`. SBPE's old PyPy cannot replace the Python runtime needed by the server and editor.
- No editor window: check that `python/tcl` and `python/tkinter` were also extracted, then inspect `userdata/logs/`.
- Source-only users and macOS editor previews need their own Python 3.10+ with Tk. The Windows source launcher can also use an installed Python or `SBPE/python/python.exe`.
- A patch reports an unexpected version or byte sequence: the client binary does not match this patch. Do not overwrite it with a mismatched patch.
- These are simulated encounters. Some behaviors and values differ from the original server; full-run simulations do not reproduce every original generation rule.

### Discord channel
https://discord.gg/J9YvUCuGmv

### License

Simulator code is licensed under [MIT](LICENSE). The Windows player runtime retains its Python and Tcl/Tk licenses in `python/LICENSE.txt`, `python/TCL-LICENSE.txt` and `python/TK-LICENSE.txt`. Browser packages additionally retain the standalone Python dependency licenses under `python/licenses/` and the WebSockets license inside its `.dist-info/licenses/` directory. The browser launcher downloads the original game assets separately. The StarBreak client, artwork, audio and SBPE are not included; their ownership and licenses remain with their respective owners. The native Windows edition requires your own game client and SBPE installation.


### Player updates / 玩家更新

Use **Equipment & settings → Check for updates** for a background version check. The result appears beside the button, including startup update success/failure. No popup interrupts play. A newer version is installed after closing and relaunching the simulator; restarting an encounter is not enough. Startup automatic updates remain enabled by default.

在 **装备与设置 → 检查更新** 可后台检查版本。检查结果及启动更新的成功或失败会显示在按钮旁，不弹窗打断操作。发现新版后，关闭模拟器并重新启动即可安装，重开当前局不会安装更新；启动自动更新仍默认开启。

From v20261016-r3 onward, the Windows and macOS player launchers check the public GitHub update channel at startup and install newer simulator files before launching. Player data under `userdata`, downloaded game assets, and the included `python` runtime are retained. Downloads verify HTTPS certificates and SHA-256 checksums. The updater keeps backups and restores interrupted/failed installations on the next launch. Network failures leave the installed version usable. A running launcher prevents a second updater from changing its files. Source checkouts containing `.git` are maintained through Git and skip player updates.

Older player packages need one manual download of v20261016-r3 (or newer) to acquire this updater. Later simulator updates use small downloads in the existing `update-channel` prerelease; a new complete player release is not needed each time. Runtime upgrades still require a complete player package. To disable startup updates, create `userdata/update-settings.json` containing `{"enabled":false}`; remove the file or set it to true to re-enable them. The update service receives ordinary download requests, not equipment, account information or local settings.

从 v20261016-r3 起，Windows / Mac 玩家启动器会在启动时检查 GitHub 上的公开更新频道，有新版时先更新模拟器再启动。`userdata` 下的配装、按键、草稿、已下载资源及包内 `python` 环境都会保留。下载校验 HTTPS 证书及 SHA-256；更新会留备份，失败或中断后可恢复。断网时继续使用现有版本；模拟器运行期间不会由另一个启动器替换文件。含 `.git` 的源码目录由 Git 管理，不自动覆盖。

旧版玩家包需要先手动下载一次 v20261016-r3 或之后的完整包，之后模拟器代码更新通过现有 `update-channel` 预发布频道分发小型更新包，不必每次重新发布完整玩家包；运行环境升级仍需完整玩家包。如需关闭启动更新，在 `userdata/update-settings.json` 写入 `{"enabled":false}`，删除文件或改为 true 可恢复。更新服务仅接收下载请求，不上传配装、账号或本地设置。

### Equipment and browser preferences / 装备与网页偏好

Both editors support item name/ID search, equipment sorting by ID or A–Z, and Chinese/English interface labels. Map, class, equipment and modifier/stat names always retain their original English names. Changing search/sort preserves the selected item and its modifiers. The browser remembers the selected class/slot, language, search/sort, all-class filter, keyboard settings and unsaved equipment drafts in its local data folder. **Save / Save & start** commits a draft; **Reload saved** discards it. Cached assets are reused, and browser HTTP caches revalidate content so an update does not leave stale scripts.

The browser's **Equipment & settings → Keyboard settings** uses the same defaults and validation as the native edition: hold R to restart, hold 7/8/9/0 for WF/DW/FAB/IC, and Backtick/F8 to toggle the editor. Default hold time is 2 seconds; editable from 0.5 to 5 seconds. Short presses, held-key repeats, text inputs, focus loss and hidden tabs do not accidentally repeat hold actions. Movement and attack bindings remain in the original game's Controls menu.

两个编辑器均支持物品名称 / ID 搜索、装备按 ID / 名称首字母排序与中英文界面；搜索和排序不会替换当前装备或清空词缀。网页端会在本地数据目录保留职业、槽位、语言、搜索排序、全部职业筛选、按键与未保存配装草稿。点击“保存 / 保存并开始”提交草稿，“载入已保存配装”放弃草稿。游戏资源复用本地下载缓存，浏览器缓存按内容校验，避免升级后仍加载旧脚本。

网页端“装备与设置 → 按键设置”与原生版使用相同默认值和校验：长按 R 重开，长按 7 / 8 / 9 / 0 切换 WF / DW / FAB / IC，Backtick / F8 显示隐藏编辑器。默认长按 2 秒，可改为 0.5–5 秒；短按、键盘重复、输入文字、失焦或切到后台不会反复触发。移动与攻击仍在原版游戏的 Controls 中设置。

GY / EGY Boss now default to a **0.66× spawn-rate multiplier**, in both editions and after Restore defaults. Explicit saved custom multipliers are retained.

GY / EGY 首领的默认刷怪速率改为 **0.66×**，两个版本及恢复默认配置统一使用此值；已经明确保存的自定义倍率会保留。
