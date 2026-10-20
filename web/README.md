# Browser edition / 网页版

Play the [online browser preview](https://sbre.online/singleweb/) without installing Python,
SBPE or a game launcher. Encounters run on the player's device in a Web Worker;
the website server serves resources and explicitly shared replay files only.
The downloadable local browser packages below remain available.

## English

### Online preview

Open [sbre.online](https://sbre.online/singleweb/) in a desktop browser. The first visit
loads runtime resources; later visits reuse cached assets. Equipment, practice
keys, original game Controls / Graphics / Sound, battle history and recordings
are saved in this browser on this device. Clearing site data removes those local
records; export important battles before clearing it. Local player packages and
the hosted website have separate storage.

Battle records supports replay, takeover and file import/export. **Create sharing
code** uploads only the selected finished replay and its equipment/parameters.
No recordings are uploaded automatically. An existing code is reused; **Enter
sharing code** downloads a replay into your local history. File export remains
available when sharing is unavailable. An update check reports a new web build
beside the button; refresh after your battle to use it.

The settings tabs are **Battle setup**, **Keys & zoom**, and **Identity & callouts**.
The first visit selects Chinese or English from the browser language; a manual
selection is remembered. **简体中文（包含词缀）** also translates modifier names.
Map, class and equipment names stay in English. `+` zooms in and `-` zooms out;
rebind both keys and change the step (5–50%, default 10%) in Keys & zoom.
The zoom level is saved locally. Playback preserves its recorded view; take
over to change it.

Creating a sharing code optionally accepts an English ID/nickname, remembered
for the next share. Leave it blank for **Unknown**. Reserved placeholders such
as `null`, `unknow`, and `unknown` are rejected, regardless of case. The nickname
travels with the imported record. Battle records includes **Latest shares** and
**Popular this week**; the latter counts successful imports in the past seven
days, without recounting repeat imports into the same local history.

### Player packages

Download a **player ZIP** from [Releases](https://github.com/yosukira/starbreak-singleplayer/releases/latest), not the automatic Source code archive.

| Platform | Package | Start |
| --- | --- | --- |
| Windows x64 | `starbreak-singleplayer-windows-*.zip` | `start-web.cmd` |
| Apple Silicon Mac | `starbreak-singleplayer-macos-arm64-*.zip` | `start-web.command` |
| Intel Mac | `starbreak-singleplayer-macos-x64-*.zip` | `start-web.command` |

Extract the **whole folder**, including `python`, to a writable location. The
browser edition can live anywhere; it does not need to be inside SBPE. Keep the
terminal open while playing. The launcher opens <http://127.0.0.1:8765> in your
browser; open that address manually if it does not appear. Closing the terminal
stops the service. To use the Windows native edition instead, follow the main
[README](../README.md) and run `start.cmd` from inside the SBPE installation.

Player packages include Python and WebSockets. Do not install Python separately
or move the launcher out of its folder. The Mac package is the browser battle
edition; the Windows native launcher and Tk editor are not Mac applications.
These packages are unsigned; macOS may require first-launch confirmation using
its normal Open / Privacy & Security controls.

### Equipment & settings

Open **Equipment & settings** to choose an encounter, class, MK and implant slots.
The same panel contains all 14 equipment/bag slots, item modifiers, per-projectile
base speeds, drops and supported encounter parameters. English / 简体中文 changes
the interface immediately without losing edits; the initial choice follows your
browser language. Original item names and the original game UI remain unchanged.

**Save & start** saves the draft and starts a fresh attempt. **Save** stores the
configuration for the next attempt. **Reload saved** discards unsaved edits.
**Restore defaults** immediately restores all four class loadouts, projectile
speeds and encounter settings to the shipped presets, saves them and keeps a
backup of the previous profile. It preserves language and original game key
bindings. Existing battles change when you start a new attempt.

For GY / EGY Boss, **Skip opening** defaults to off and is saved per encounter. Enabling it skips the opening robots and ship arrival; the next attempt begins with the boss ship in position and platforms ready.

**Homing missile delay (ms)** is saved separately for GY / EGY Boss: 0–500ms, default 275ms. Boss missiles and enemy homing missiles share this setting. It changes how old the player position used for steering is, not the 50ms steering interval or firing cooldown. 0ms uses the current position. Save to apply; Restore defaults returns both maps to 275ms.

**Export / Import** shares the browser profile as JSON. Import is a draft until
saved. Browser exports use their own schema, separate from native editor exports.
Unsaved drafts survive a page reload. If another tab has saved a newer version,
export your draft before reloading; stale tabs cannot overwrite it.

The game uses its own keyboard and interaction settings. Click the game canvas
to focus it. The wrapper adds no movement or combat bindings.

### Supported encounters and limits

The selector currently enables 26 scenes: regional Mid/Boss encounters, wave
practice, Star Cathedral encounters, Home and the Elite Graveyard full-run
preview. Five other full runs remain disabled pending their room-specific
adapters. Enabled does not mean every encounter has been fully playtested.

Mac browser checks covered all four shells, movement, attacks, incoming damage,
death/restart, equipment changes, a second region and a fresh inventory handshake
from Elite Graveyard to Home retaining 14 items and modifiers. A full Elite
Graveyard clear and every encounter's retail accuracy remain unverified.
Full-run previews are simulations and do not reproduce every original rule.
Browser ground-item persistence across rooms is not implemented; carry items in
your inventory when leaving a room. Native mode has the same public baseline.

First use requires Internet access to the official asset server and the SoundJS
CDN. The launcher downloads the pinned browser client, verifies its SHA256 and
caches it under `userdata/web/`. Required scene textures are prepared before the
game opens; audio loads on demand. The original game client/art/audio are not
shipped in these packages. This is not a fully offline edition.

Browser settings are stored separately in `userdata/web/profile.json`; native
presets are copied on the first browser launch and then edited independently.
Reset backups are `userdata/web/profile.before-restore-defaults-*.json`.
No original-server account login is used. The service binds localhost for one
local player. Do not expose it directly to the Internet.

### Running from source

Use Python **3.11+** and install the pinned dependency:

```sh
python3 -m pip install -r web/requirements.txt
python3 tools/web_app.py
```

Options: `--no-browser`, `--port 8765`, `--ws-port 8766`, `--game-port 20480`,
`--bridge-port 21551`, `--data-dir /path/to/browser-profile`, and
`--client /path/to/VR1574197275.mvmmoclient.js`. Only the SHA-pinned client is
accepted. All four ports must be free. `--check-runtime` verifies dependencies
without downloading the game or starting a battle.

If startup fails, read the terminal message and `userdata/web/server.log`.
A first-download error requires connectivity to `starbreak.com`; it is not a
Python-not-installed error in the complete player package.

## 中文

设置分为“地图与配装”“地图与敌人参数”“按键与缩放”三个页签。
首次按浏览器语言选中英文，手动选择会保留；“简体中文（包含词缀）”额外翻译词缀，
地图、角色和装备名称保持英文。默认 `+` 放大画面、`-` 扩大视野，按键和每次缩放比例
均可设置（5–50%，默认 10%），缩放状态保存在本机。回放沿用录制时视野，接管后可调整。

生成分享码时可选填英文 ID／昵称，下次自动填入；留空显示 **Unknown**。
`null`、`unknow`、`unknown` 等占位名不可使用，不区分大小写。昵称会保留在导入的战绩中。
战斗记录的“最新分享”和“近 7 天热门”可浏览并导入他人的公开分享；热度按成功导入统计，
同一本地记录重复导入不反复计数。


### 在线试玩

直接打开 [sbre.online](https://sbre.online/singleweb/)，无需安装 Python、SBPE 或启动器。
首次加载会下载运行资源，之后复用浏览器缓存。战斗计算在玩家设备上运行；
装备配置、练习快捷键、游戏内 Controls / Graphics / Sound、战绩和录像保存在当前浏览器。
清理网站数据会清除本地记录，重要录像请先导出。网站与本地玩家包各自保存数据。

在战斗记录中可以回放、接管和导入导出。只有点击 **生成分享码** 才上传选中的
已结束录像及其配装参数，不会自动上传其他录像；已有分享码会复用。
**输入分享码** 会下载录像并加入本地记录。分享不可用时仍可导出文件。
检查更新会在按钮旁提示；结束本场后刷新网页即可使用新版本。

### 玩家启动

从 [Releases](https://github.com/yosukira/starbreak-singleplayer/releases/latest)
下载对应玩家包，**不要下载 Source code**。Windows 使用
`starbreak-singleplayer-windows-*.zip`，Apple 芯片 Mac 使用
`macos-arm64`，Intel Mac 使用 `macos-x64`。

完整解压到可写目录，保留 `python` 文件夹：Windows 双击 **start-web.cmd**，
Mac 双击 **start-web.command**。会自动打开 <http://127.0.0.1:8765>；若未打开，
手动访问此地址。游玩期间保留终端窗口，关闭终端会停止服务。网页版可以放在
任何目录，**不用放进 SBPE**；Windows 原生版仍按主 README 放进 SBPE，运行
`start.cmd`。玩家包已包含运行环境，不需另装 Python。Mac 包用于浏览器战斗，
不包含可在 Mac 上运行的 Windows 原生游戏或 Tk 编辑器。包未签名，macOS
首次启动可能需要通过系统常规的“打开”或“隐私与安全性”确认。

### 装备与设置

地图、职业、MK、植入体槽、14 个装备/背包槽、词缀、基础弹速、掉落和已支持的
战斗参数都在 **装备与设置** 中。语言默认跟随浏览器，可切换中文/英文，未保存
编辑会保留；原版游戏本身的界面与物品名称保留原样。

- **保存并开始**：保存当前草稿并开新一局。
- **保存**：保存配置，下一局使用。
- **载入已保存配装**：丢弃未保存编辑。
- **恢复默认配置**：立即将四职业配装、弹速与战斗设置全部恢复为发布预设并保存，
  自动备份原配置，语言与游戏按键保留；下一局生效。
- **导出/导入**：分享网页版 JSON 配置，导入后需保存；格式与桌面版导出不同。

GY / EGY 首领的 **跳过开局** 默认关闭，两图分别保存。开启后，新一局直接从首领飞船停稳、平台就绪的阶段开始，跳过大机器人及进场；恢复默认配置会关闭它。

**追踪弹延迟 (ms)** 在 GY / EGY 分别保存，范围 0–500ms，默认 275ms。Boss 与小怪的追踪弹共用此设置。它决定转向时瞄准玩家多久以前的位置，0ms 表示当前位置；不改变 50ms 的转向更新间隔或发射 CD。保存后生效，恢复默认配置会将两图都恢复到 275ms。

刷新会保留未保存草稿；多标签页发生保存冲突时，先导出草稿再重新载入。
先点击画面获得焦点，场内操作沿用原版游戏自己的按键设置。

### 支持范围

目前可选 26 个场景，包括各区域 Mid/Boss、波次、星之大教堂、家园，以及
精英墓地整关试玩。另五个整关仍显示适配中，暂不可选。可选不代表每个场景
都已完整实测；整关试玩与原服规则仍可能不同。

Mac 浏览器已验证四职业、移动/攻击/受伤、死亡重开、改配装、跨区域资源，
以及精英墓地携带 14 件装备和词缀返回家园的实时背包交接；整关通关与全部
关卡行为仍待玩家验证。跨房间的地面物品保留尚未实现，离开前请拾入背包；
公开原生版也是同样的基础实现。

首次使用需联网访问原版资源与 SoundJS CDN；客户端核对 SHA256 后缓存，
场景贴图准备完再进入游戏，音频按需加载。包不含原版客户端、美术和音频，
也不是完全离线版本。无需原服账号。服务仅监听本机，供一名本地玩家使用。

网页版配装在 `userdata/web/profile.json`，首次从已有原生配装复制，此后独立
保存。恢复默认的备份位于同目录 `profile.before-restore-defaults-*.json`。
源码运行需要 Python 3.11+，安装与命令见上方；启动失败可看终端和
`userdata/web/server.log`。


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

## Textures & enhancements / 贴图与增强

The browser edition has a separate **Textures & enhancements** tab. Select replacement
PNG files with the original filenames and dimensions shown on the page. Files stay in
this browser; they are not uploaded. Remove individual textures or clear them all.
Changes apply to the next battle or replay. Enemy health, the room minimap, and an
alternative upper-left health/ammo HUD have separate switches, off by default.

网页版顶部的“贴图与增强”支持选择自己的 PNG 贴图，名称与尺寸需匹配页面列出的原图。
文件只保存在当前浏览器，不上传服务器；支持单张删除、清空，下一次战斗或回放生效。
敌人血量、小地图、左上角生命与弹药显示有独立开关，默认关闭。

Local history supports deleting finished battles and recordings, individually or in
batches, with 10/50/100 records per page (default 50). Server shares are independent.
Character and guild names are configured in **Identity & callouts**; the character name
is the default signature for sharing and comments. Victory announcements use that name
and optional guild.

本地战绩支持单条、批量删除及每页 10/50/100 条（默认 50）；删除本地记录不影响服务器分享。
在“角色与提示”保存角色名、公会名与通关提示。角色名用作分享、评论的默认署名；背包和
通关提示会使用设置的名称与可选公会名。

SBPE-style display options are grouped into HUD, Noise, Misc and Minimap. Enemy
health is a cached number at the body center, with positive armor below it; no
extra bars are added. Noise controls effects, damage numbers, names/guilds, original
bars, friendly summons, your shell, chat, shake and flashes. Misc controls the
camera, scenery, solid background, brightness, idle cursor and debug boxes. Optional
compact health/ammo, EC gains, FPS and room information are also available.
Display settings apply immediately and do not change combat values or replay input.
The low-clutter preset is optional; resetting enhancements preserves custom textures.

增强设置按 HUD、Noise、Misc 和小地图分组。敌人血量以缓存数字显示在身体中央，
正护甲在下方，不增加血条。Noise 可分别隐藏特效、伤害数字、名字/公会、原版血条、
友方召唤物、自身外观和聊天，关闭震屏/闪屏并调节受击闪白。Misc 提供视角、场景、
纯色背景、背景亮度、鼠标自动隐藏和调试框设置；另有角色旁生命/弹药、EC 增量、FPS
及房间信息。显示设置立即生效，不修改战斗数值或录像输入；重置增强会保留贴图。

Existing equipment with more than 64 modifiers opens and saves without truncating
or merging repeated modifiers. This also applies to history and replay exports.
The local editor service includes its WebSocket library in source updates, so an
older portable Python installation can open equipment and history offline.

已有装备超过 64 条词缀也可直接打开、编辑和保存，重复词缀不截断、不合并；
战绩与录像导出同样保留。更新包自带本地服务所需的 WebSocket 库，旧便携 Python
也能离线打开装备与战绩窗口。
