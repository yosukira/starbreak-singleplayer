# Browser battle prototype / 网页战斗原型

Runs the original 1.14.6 browser client against the existing local battle
simulator. No Windows game process, SBPE, native injection or original account
login is involved. The Python battle service runs locally; this is not yet an
offline, browser-only release.

## Run

From the project folder, using Python 3.10+:

```sh
python3 -m pip install -r web/requirements.txt
python3 tools/web_app.py
```

Open <http://127.0.0.1:8765>, select a shell and weapon, then enter the arena.
The launcher downloads the pinned original browser client from `starbreak.com`
on first use, verifies its SHA256 and caches it under `userdata/web/`.
Textures are downloaded on demand and cached locally. An existing client can
instead be supplied with `--client /path/to/VR1574197275.mvmmoclient.js`.
The original client and assets are not included in this source repository.

The current encounter is **Elite Graveyard Mid** (`hulk_mid`). Arrow keys move,
Space jumps and D fires the main weapon using the original client's defaults.
Click the game canvas to focus it. Select a new shell/weapon and click Enter
arena to restart. Closing the terminal stops the local service.

The interface supports English and Chinese, with browser-language defaults.
Existing local presets are copied into an isolated web-session loadout before
applying the selected weapon; original loadout files are preserved.

The service listens on localhost and is intended for one local player. Other
encounters, full-run inventory transfers, complete browser equipment editing,
offline packaging and public multi-user hosting are not supported yet.

## 中文

这是连接现有 Python 模拟器的**真实网页战斗原型**，无需 Windows 客户端、
SBPE 或原服账号。首次启动需下载原版网页客户端及贴图；目前仍需要本地
Python 服务，不是完全离线、只靠浏览器运行的正式版。

在项目目录执行上面的安装与启动命令，然后打开 <http://127.0.0.1:8765>。
Mac 也可以在依赖安装完成后运行 `start-web.command`。选择职业和主武器，
点击“进入练习场”；更换选择后再次进入即可重开。先点击游戏画面取得焦点。
默认方向键移动、Space 跳跃、D 主武器攻击。

首批支持 **Elite Graveyard Mid**；整关背包交接、其他场景、完整网页装备
编辑器、离线玩家包和多人公网部署仍待适配。界面支持中英文并默认跟随
浏览器语言。网页会话使用单独的配装副本，不会覆盖原有本地配装。
