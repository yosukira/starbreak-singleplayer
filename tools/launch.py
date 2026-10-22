"""Keep updates outside running game processes; then start the requested edition."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from update import application_lock,auto_update,recover

# A hand-placed local snapshot is not part of the published update channel. It
# records that intent in userdata/local-build.json so launching it never waits
# on a download the owner did not ask for.
LOCAL_ONLY=ROOT/'userdata/local-build.json'


def main():
    mode,*arguments=sys.argv[1:]
    if mode not in ('web','native'):raise SystemExit('Unknown launch mode')
    isolated=['-I'] if sys.flags.isolated else []
    command=[sys.executable,*isolated,str(ROOT/'tools'/('web_app.py' if mode=='web' else 'play_hulk.py')),*arguments]
    # Runtime checks are offline and leave launch/update state untouched.
    if '--check-runtime' in arguments:return subprocess.call(command,cwd=ROOT)
    if LOCAL_ONLY.is_file():
        print('Local build: update channel skipped / 本地版：跳过在线更新',flush=True)
        return subprocess.call(command,cwd=ROOT)
    try:
        with application_lock(ROOT):
            try:recover(ROOT)
            except Exception as error:
                print('Could not restore the previous update / 无法恢复上次更新: '+str(error),flush=True)
                return 1
            try:
                from startup_progress import run
                print(run(lambda progress:auto_update(ROOT,progress=progress)),flush=True)
            except Exception as error:
                print('Update unavailable; using installed version / 暂时无法更新，使用现有版本: '+str(error),flush=True)
            print('Starting game / 正在启动游戏…',flush=True)
            return subprocess.call(command,cwd=ROOT)
    except (OSError,BlockingIOError) as error:
        print('Another instance is running, or this folder is not writable. / 模拟器已在运行，或目录无法写入。',flush=True)
        return 1

if __name__=='__main__':
    from startup_progress import console_selection_guard
    with console_selection_guard():raise SystemExit(main())
