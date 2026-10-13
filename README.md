# StarBreak Singleplayer

[中文](#中文使用说明) · [English](#english-usage)

StarBreak 的本地单人战斗模拟器 / A local singleplayer battle simulator for StarBreak.

## 中文使用说明

### 功能与运行环境

- 多个区域的 Mid、Boss、波次挑战和整关试玩，可在装备编辑器里选择。
- 四职业切换、MK、装备、词缀、植入体槽、补给掉率，以及部分地图的炮台和数值设置。
- 装备编辑器支持简体中文、English 和跟随系统；切换语言会保留当前未保存的编辑。
- **运行游戏需要 Windows、StarBreak Windows 客户端和兼容的 [SBPE](https://github.com/atomizer/sbpe) 安装。** 客户端补丁针对 StarBreak 1.14.6 的 Windows 二进制；其他版本可能不兼容。
- **Windows 玩家 Release 已自带 Python 和 Tk，无需另外安装 Python 或配置 PATH。** 游戏内注入仍使用 SBPE 自带的 32 位 PyPy。GitHub 的 Source code / Download ZIP 是源码包，不包含这套运行环境。
- Mac 可以独立预览装备编辑器，不能用这份启动脚本运行游戏。

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
2. 在左侧选择槽位，在右侧选择物品并编辑词缀。物品名称保留原版英文；界面、地图和属性标签随语言切换。
3. 点击 **应用/保存**。服务器检测到配装变化后会重开当前局。地图、掉落率等设置通过 **保存设置** 保存。
4. 右上角选择 **System / 跟随系统**、**简体中文** 或 **English**。首次默认跟随系统：Windows 中文界面使用中文，其他语言使用英文；选择会在本机保存。
5. **导出 / 导入** 用于分享 JSON 配装；导入后点击应用/保存才会写入配装。

选择有明确弹速参数的武器后，可在 **子弹基础速度** 中修改每种弹体的基础速度（整数 `0–1000000`）。同一物品的全部实例共用此设置；原有速度词缀继续保留，存活时间不变，因此射程也可能改变。数值沿用原版武器参数，不统一标作 px/s；`0` 是零基础速度，不是恢复默认。点 **恢复原值** 可还原。

**修改弹速后点击应用/保存，再退出游戏并重新运行 `start.cmd`；长按 R 不会重新加载基础弹速。** 弹速设置会随配装导入/导出，不会改写原始物品目录。

| 操作 | 按键 |
| --- | --- |
| 显示 / 隐藏编辑器（Windows） | `~` 所在键或 `F8` |
| 重开当前局 | 长按 `R` |
| 切职业 WF / DW / FAB / IC | 分别长按 `7` / `8` / `9` / `0` |

Mac 编辑器预览：安装带 Tk 的 Python 3.10+ 后，在项目目录运行 `python3 tools/gear_editor.py --show`，或运行 `preview-editor.command`。Mac 预览的保存会写入本机配置，Windows 全局热键及游戏效果不在预览范围内。

### 本地数据与常见问题

- 首次启动自动创建 `userdata/`，保存自己的配装、语言偏好和日志；删除此目录可恢复默认设置。它不会进入 Git 版本控制。
- 不需要原服账号登录。本地玩家采用固定的虚拟身份，原服账号进度与背包不会导入。
- 提示缺少 `remote.bin`、`symbols.json` 或 `pypy3.exe`：检查项目是否放在完整的 SBPE 安装目录下。
- 提示 Python 未找到或运行环境检查失败：重新完整解压 Windows 玩家包，确认 `start.cmd` 旁有 `python/python.exe`。SBPE 的旧版 PyPy 不能代替本地服和编辑器所需的 Python。
- 编辑器没有打开：确认玩家包的 `python/tcl`、`python/tkinter` 也已解压，并查看 `userdata/logs/` 中的日志。
- 只下载源码或在 Mac 预览：自行准备带 Tk 的 Python 3.10+；Windows 源码启动器也可识别系统 Python 或 SBPE 目录下的 `python/python.exe`。
- 补丁提示版本或字节不匹配：当前客户端二进制不符合补丁预期，不要用不匹配的补丁强行覆盖。
- 地图和机制为模拟实现，部分行为及数值可能与原服不同。整关试玩不代表全部原版生成规则已经恢复。

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
2. Select an equipment slot on the left, then choose an item and edit its modifiers on the right. Item names retain their original English names; UI labels, map names and stat labels follow the selected language.
3. Click **Apply / Save**. The server restarts the current run when it detects the new loadout. Use **Save settings** for map selection, drop rates and other map settings.
4. Select a language in the top-right corner. The first launch follows your system language: Chinese Windows UI locales use Chinese; other locales use English. Your selection is saved locally.
5. **Export / Import** shares JSON loadouts. After importing, click Apply / Save to write the equipment changes.

Weapons with an explicit speed parameter expose **Base projectile speed** for each projectile. Enter an integer from `0` to `1000000`. The setting applies to every instance of that item; existing speed modifiers remain, and lifetime is unchanged, so range may change. Values use the original weapon parameters and are not universally pixels per second. `0` means zero base speed, not the default. Use **Reset to default** to restore the original value.

**After saving a projectile speed change, exit the game and relaunch `start.cmd`; holding R does not reload base speeds.** Speed settings are included in loadout import/export and never rewrite the original item catalogue.

| Action | Key |
| --- | --- |
| Show / hide the editor (Windows) | The key left of `1` or `F8` |
| Restart the run | Hold `R` |
| Switch to WF / DW / FAB / IC | Hold `7` / `8` / `9` / `0`, respectively |

To preview the editor on macOS, install Python 3.10+ with Tk and run `python3 tools/gear_editor.py --show` from the project folder, or run `preview-editor.command`. Saving in this preview writes local settings; Windows global hotkeys and in-game effects cannot be verified there.

### Local files and troubleshooting

- First launch creates `userdata/` for your own loadouts, language preference and logs. Delete that folder to restore defaults. It is excluded from Git.
- No original-server account login is required. A synthetic local player is used; online account progress and inventory are not imported.
- Missing `remote.bin`, `symbols.json` or `pypy3.exe`: check the project folder is inside a complete SBPE installation.
- Python missing or runtime check failed: re-extract the complete Windows player ZIP and make sure `python/python.exe` is next to `start.cmd`. SBPE's old PyPy cannot replace the Python runtime needed by the server and editor.
- No editor window: check that `python/tcl` and `python/tkinter` were also extracted, then inspect `userdata/logs/`.
- Source-only users and macOS editor previews need their own Python 3.10+ with Tk. The Windows source launcher can also use an installed Python or `SBPE/python/python.exe`.
- A patch reports an unexpected version or byte sequence: the client binary does not match this patch. Do not overwrite it with a mismatched patch.
- These are simulated encounters. Some behaviors and values differ from the original server; full-run simulations do not reproduce every original generation rule.

### License

Simulator code is licensed under [MIT](LICENSE). The Windows player runtime retains its Python and Tcl/Tk licenses in `python/LICENSE.txt`, `python/TCL-LICENSE.txt` and `python/TK-LICENSE.txt`. The StarBreak client, artwork, audio and SBPE are not included; their ownership and licenses remain with their respective owners. You must supply your own client and SBPE installation.
