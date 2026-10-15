#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if [ -x ./python/bin/python3 ]; then
  unset PYTHONHOME PYTHONPATH
  exec ./python/bin/python3 -I tools/web_app.py "$@"
fi
if command -v python3 >/dev/null 2>&1 && python3 -c 'import websockets.sync.server' >/dev/null 2>&1; then
  exec python3 tools/web_app.py "$@"
fi
printf '%s\n' 'Python runtime missing. Download the complete macOS player ZIP from GitHub Releases.' '缺少运行环境。请下载 GitHub Releases 中的完整 macOS 玩家包。'
read -r -p 'Press Enter to close / 回车关闭' _
exit 1
