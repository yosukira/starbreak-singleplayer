"""Verified source updates at launch; player data and portable runtime are excluded."""
import argparse
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# A public download channel keeps private hosting details out of player builds.
UPDATE_CHANNEL = 'https://github.com/yosukira/starbreak-singleplayer/releases/download/update-channel'
DOWNLOAD_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


def download_url(url):
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.hostname not in DOWNLOAD_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None,443)):
        raise ValueError('Untrusted update download URL')
    return url


class DownloadRedirects(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        download_url(newurl)
        return super().redirect_request(request,fp,code,msg,headers,newurl)

MAX_DOWNLOAD = 32*1024*1024
DIRECTORIES = {'data','localserver','maps','plugins','tools','web'}
FILES = {'README.md','LICENSE','start.cmd','start-web.cmd','start-web.command','preview-editor.command'}


def managed(name):
    path=PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or path.is_absolute()
            or any(part in ('','.','..') for part in name.split('/'))):
        raise ValueError('Unsafe update path')
    if path.parts[0] not in DIRECTORIES and name not in FILES:
        raise ValueError('Update includes protected files: '+name)
    return path


def atomic_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,delete=False) as stream:
        tmp=Path(stream.name)
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    try:os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def version(root=ROOT):
    try:return json.loads((root/'data/version.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {'version':'source','sequence':0}


def fetch(path, limit, progress=None):
    if not re.fullmatch(r'latest\.json|starbreak-singleplayer-update-v[0-9]{8}-r[0-9]+\.zip',path):
        raise ValueError('Invalid update download name')
    opener=build_opener(HTTPSHandler(context=ssl.create_default_context()),DownloadRedirects())
    with opener.open(download_url(UPDATE_CHANNEL+'/'+path),timeout=10) as response:
        download_url(response.geturl())
        total=int(response.headers.get('Content-Length') or 0);chunks=[];received=0
        reader=response.read1 if isinstance(response,http.client.HTTPResponse) else response.read
        if progress:progress(0,total)
        while True:
            # Return available bytes promptly instead of waiting for a whole
            # 64 KB block before the download display can move.
            chunk=reader(min(16*1024,limit+1-received))
            if not isinstance(chunk,(bytes,bytearray)):raise ValueError('Invalid update download data')
            if not chunk:break
            received+=len(chunk)
            if received>limit:raise ValueError('Update download too large')
            chunks.append(chunk)
            if progress:progress(received,total)
    return b''.join(chunks)


def manifest():
    value=json.loads(fetch('latest.json',16384))
    if (value.get('schema')!=1 or type(value.get('sequence')) is not int or value['sequence']<=0
            or not re.fullmatch(r'v[0-9]{8}-r[0-9]+',str(value.get('version','')))
            or not re.fullmatch(r'[a-f0-9]{64}',str(value.get('sha256','')))
            or type(value.get('size')) is not int or not 0<value['size']<=MAX_DOWNLOAD
            or not re.fullmatch(r'starbreak-singleplayer-update-v[0-9]{8}-r[0-9]+\.zip',str(value.get('file','')))):
        raise ValueError('Invalid update manifest')
    return value


def destination(root,name):
    relative=managed(name)
    target=root/relative
    if root.resolve() not in target.resolve().parents:
        raise ValueError('Update destination leaves project')
    # Do not replace user-created symlinks, even when they point inside the tree.
    if any((root/Path(*relative.parts[:i])).is_symlink() for i in range(1,len(relative.parts)+1)):
        raise ValueError('Symlink update destination rejected')
    return target


def recover(root=ROOT):
    journal=root/'userdata/updates/pending.json'
    if not journal.is_file():return
    value=json.loads(journal.read_text(encoding='utf8'))
    folder=value['backup']
    if not re.fullmatch(r'backup-[a-zA-Z0-9_-]+',folder):raise ValueError('Invalid update journal')
    backup=journal.parent/folder
    for name in value['files']:
        target=destination(root,name)
        if name in value['originals']:
            original=backup/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(original,target)
        else:target.unlink(missing_ok=True)
    journal.unlink()


def apply_update(data, info, root=ROOT, progress=None):
    report=lambda state,current,total:progress(state,current,total,info['version']) if progress else None
    report('verifying',0,1)
    if len(data)!=info['size'] or hashlib.sha256(data).hexdigest()!=info['sha256']:
        raise ValueError('Update checksum mismatch')
    recover(root)
    payload={}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total=0
        entries=archive.infolist()
        for index,item in enumerate(entries,1):
            if item.is_dir():continue
            if not item.filename.startswith('starbreak-singleplayer/'):
                raise ValueError('Invalid update root')
            name=item.filename[len('starbreak-singleplayer/'):]
            destination(root,name)
            if name in payload or stat.S_ISLNK(item.external_attr>>16):raise ValueError('Unsafe ZIP entry')
            total+=item.file_size
            if total>128*1024*1024 or len(payload)>=5000:raise ValueError('Update expands too large')
            content=archive.read(item)
            if name.endswith('.py'):compile(content,name,'exec')
            payload[name]=(content,(item.external_attr>>16)&0o777)
            report('verifying',index,len(entries))
    embedded=json.loads(payload['data/version.json'][0])
    if (embedded.get('sequence'),embedded.get('version'))!=(info['sequence'],info['version']):
        raise ValueError('Update version mismatch')
    if not {'tools/launch.py','tools/update.py','tools/web_app.py'}<=payload.keys():
        raise ValueError('Incomplete update')
    state=root/'userdata/updates';state.mkdir(parents=True,exist_ok=True)
    backup=Path(tempfile.mkdtemp(prefix='backup-',dir=state))
    originals=[]
    report('backing_up',0,len(payload))
    for index,name in enumerate(payload,1):
        target=destination(root,name)
        if target.exists() and not target.is_file():raise ValueError('Update target is not a file')
        if target.is_file():
            copy=backup/name;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,copy)
            originals.append(name)
        report('backing_up',index,len(payload))
    journal=state/'pending.json'
    atomic_json(journal,dict(backup=backup.name,files=list(payload),originals=originals))
    try:
        report('installing',0,len(payload))
        for index,name in enumerate(sorted(payload,key=lambda name:name=='data/version.json'),1):
            content,mode=payload[name];target=destination(root,name)
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as stream:
                temporary=Path(stream.name);stream.write(content)
            try:
                if mode:temporary.chmod(mode)
                os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
            report('installing',index,len(payload))
        journal.unlink()
    except BaseException:
        recover(root)
        raise
    return backup


@contextmanager
def application_lock(root=ROOT):
    path=root/'userdata/updates/application.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


STATUS_STATES={'idle','checking','downloading','installing','current','ahead','available','updated',
               'failed','full_required','disabled','source','source_update','interrupted'}


def read_status(root=ROOT):
    result=dict(state='source' if (root/'.git').exists() else 'idle',
                current_version=version(root)['version'],latest_version='',updated_at=0)
    try:
        saved=json.loads((root/'userdata/updates/status.json').read_text(encoding='utf8'))
        if (saved.get('state') in STATUS_STATES
                and saved.get('current_version')==result['current_version']):
            candidate=dict(result,**saved)
            if candidate['state'] in ('checking','downloading','installing') and time.time()-float(candidate['updated_at'])>120:
                candidate['state']='interrupted'
            return candidate
    except (OSError,ValueError,TypeError,AttributeError):pass
    # Older launchers installed this UI before they knew how to save a result.
    # A fresh completed backup plus an advanced installed version identifies that first update.
    if not (root/'.git').exists() and not (root/'userdata/updates/pending.json').exists():
        try:
            backups=sorted((root/'userdata/updates').glob('backup-*'),key=lambda p:p.stat().st_mtime,reverse=True)
            if backups and 0<=time.time()-backups[0].stat().st_mtime<300:
                previous=version(backups[0])
                if (version(root).get('sequence',0)>previous.get('sequence',0)>0
                        and (root/'data/version.json').stat().st_mtime>=backups[0].stat().st_mtime):
                    return record_status('updated',root,latest_version=result['current_version'],operation='auto')
        except (OSError,ValueError,TypeError):pass
    return result


def record_status(state, root=ROOT, **fields):
    result=dict(state=state,current_version=version(root)['version'],latest_version='',updated_at=time.time())
    result.update(fields)
    try:atomic_json(root/'userdata/updates/status.json',result)
    except OSError:pass  # An unwritable status file must not prevent the game from starting.
    return result


def status_message(result, language='en'):
    messages={
        'idle':('启动时自动检查更新。','Updates are checked automatically at startup.'),
        'checking':('正在检查更新…','Checking for updates…'),
        'downloading':('正在下载新版本 {latest}…','Downloading new version {latest}…'),
        'installing':('正在更新至 {latest}…','Installing {latest}…'),
        'current':('已是最新版。','Up to date.'),
        'ahead':('本地版本高于更新频道的 {latest}。','Local version is newer than the update channel ({latest}).'),
        'available':('发现新版 {latest}；关闭模拟器并重新启动后自动更新。','Update {latest} available. Close and relaunch the simulator to install.'),
        'updated':('已成功更新至 {current}。','Successfully updated to {current}.'),
        'failed':('检查或更新失败，请稍后重试；继续使用当前版本。','Update check or installation failed. Try again later; the installed version remains in use.'),
        'full_required':('发现新版 {latest}；请下载完整玩家包。','Update {latest} requires downloading a complete player package.'),
        'disabled':('已关闭启动自动更新，仍可手动检查。','Startup updates are disabled; manual checks are available.'),
        'source':('源码目录通过 Git 更新；可手动检查版本。','Source checkout: update through Git; manual version checks are available.'),
        'source_update':('发现新版 {latest}；源码目录请通过 Git 更新。','Update {latest} available. Update this source checkout through Git.'),
        'interrupted':('上次检查或更新未完成，请重试。','The previous update check or installation was interrupted. Please retry.'),
    }
    current=str(result.get('current_version','source'))
    latest=str(result.get('latest_version',''))
    text=messages.get(result.get('state'),messages['idle'])[0 if language=='zh' else 1]
    return (('当前版本 ' if language=='zh' else 'Version ')+current+' · '
            +text.format(current=current,latest=latest))


def check_for_updates(root=ROOT):
    """Read-only checks are safe while either edition is running."""
    record_status('checking',root,operation='manual')
    try:
        info=manifest()
        installed=version(root).get('sequence',0)
        state=('ahead' if info['sequence']<installed else 'current' if info['sequence']==installed else
               'source_update' if (root/'.git').exists() else
               'full_required' if installed<info.get('minimum_sequence',0) else 'available')
        return record_status(state,root,latest_version=info['version'],operation='manual')
    except Exception as error:
        return record_status('failed',root,operation='manual',detail=str(error)[:500])


def public_status(result):
    value={key:result.get(key) for key in ('state','current_version','latest_version','updated_at')}
    value['messages']={language:status_message(result,language) for language in ('en','zh')}
    return value


class UpdateChecker:
    """One background request per UI process; no game-file writes or GUI callbacks."""
    def __init__(self, root=ROOT):
        self.root=root
        self.lock=threading.Lock()
        self.thread=None
        self.result=None

    @property
    def busy(self):return self.thread is not None and self.thread.is_alive()

    def status(self):return self.result or read_status(self.root)

    def start(self):
        with self.lock:
            if not self.busy:
                self.result=record_status('checking',self.root,operation='manual')
                self.thread=threading.Thread(target=self._run,daemon=True)
                self.thread.start()
            return self.status()

    def _run(self):self.result=check_for_updates(self.root)


class CompletePackageRequired(ValueError):pass


def auto_update(root=ROOT, check_only=False, progress=None):
    info={}
    try:
        recover(root)
        # Source checkouts are maintained through Git, never overwritten by the player updater.
        if (root/'.git').exists():
            record_status('source',root,operation='auto')
            return 'Git checkout; automatic player updates skipped.'
        preference=root/'userdata/update-settings.json'
        if preference.is_file() and json.loads(preference.read_text()).get('enabled') is False:
            record_status('disabled',root,operation='auto')
            return 'Automatic updates disabled / 已关闭自动更新'
        record_status('checking',root,operation='auto')
        if progress:progress('checking',0,0,'')
        info=manifest()
        if info['sequence']<=version(root).get('sequence',0):
            record_status('ahead' if info['sequence']<version(root).get('sequence',0) else 'current',
                          root,latest_version=info['version'],operation='auto')
            return 'Already up to date / 已是最新版'
        if version(root).get('sequence',0)<info.get('minimum_sequence',0):
            raise CompletePackageRequired('Download the complete player package first / 请先下载新版完整玩家包')
        if check_only:
            record_status('available',root,latest_version=info['version'],operation='auto')
            return 'Update available / 可更新: '+info['version']
        record_status('downloading',root,latest_version=info['version'],operation='auto',current=0,total=info['size'])
        if progress:progress('downloading',0,info['size'],info['version'])
        last_report=[0.]
        def downloading(current,total):
            now=time.monotonic()
            if now-last_report[0]>=.2 or current==info['size']:
                record_status('downloading',root,latest_version=info['version'],operation='auto',current=current,total=info['size']);last_report[0]=now
            if progress:progress('downloading',current,info['size'],info['version'])
        data=fetch(info['file'],MAX_DOWNLOAD,downloading) if progress else fetch(info['file'],MAX_DOWNLOAD)
        record_status('installing',root,latest_version=info['version'],operation='auto')
        if progress:apply_update(data,info,root,progress=progress)
        else:apply_update(data,info,root)
        record_status('updated',root,latest_version=info['version'],operation='auto')
        return 'Updated / 已更新: '+info['version']
    except Exception as error:
        record_status('full_required' if isinstance(error,CompletePackageRequired) else 'failed',root,
                      latest_version=info.get('version',''),operation='auto',detail=str(error)[:500])
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    options=parser.parse_args()
    with application_lock():print(auto_update(check_only=options.check))

if __name__=='__main__':main()
