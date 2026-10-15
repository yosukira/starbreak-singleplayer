# Browser edition / 网页版

The original StarBreak 1.14.6 browser client runs against a local Python battle
service. Windows and macOS can play without SBPE or a Windows game install.
This is a local, singleplayer browser edition, not a hosted multiplayer service.

## English

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
