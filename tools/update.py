"""Verified source updates at launch; player data and portable runtime are excluded."""
import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import tempfile
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
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    os.replace(tmp,path)


def version(root=ROOT):
    try:return json.loads((root/'data/version.json').read_text(encoding='utf8'))
    except (OSError,ValueError):return {'version':'source','sequence':0}


def fetch(path, limit):
    if not re.fullmatch(r'latest\.json|starbreak-singleplayer-update-v[0-9]{8}-r[0-9]+\.zip',path):
        raise ValueError('Invalid update download name')
    opener=build_opener(HTTPSHandler(context=ssl.create_default_context()),DownloadRedirects())
    with opener.open(download_url(UPDATE_CHANNEL+'/'+path),timeout=10) as response:
        download_url(response.geturl())
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('Update download too large')
    return data


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


def apply_update(data, info, root=ROOT):
    if len(data)!=info['size'] or hashlib.sha256(data).hexdigest()!=info['sha256']:
        raise ValueError('Update checksum mismatch')
    recover(root)
    payload={}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total=0
        for item in archive.infolist():
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
    embedded=json.loads(payload['data/version.json'][0])
    if (embedded.get('sequence'),embedded.get('version'))!=(info['sequence'],info['version']):
        raise ValueError('Update version mismatch')
    if not {'tools/launch.py','tools/update.py','tools/web_app.py'}<=payload.keys():
        raise ValueError('Incomplete update')
    state=root/'userdata/updates';state.mkdir(parents=True,exist_ok=True)
    backup=Path(tempfile.mkdtemp(prefix='backup-',dir=state))
    originals=[]
    for name in payload:
        target=destination(root,name)
        if target.exists() and not target.is_file():raise ValueError('Update target is not a file')
        if target.is_file():
            copy=backup/name;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,copy)
            originals.append(name)
    journal=state/'pending.json'
    atomic_json(journal,dict(backup=backup.name,files=list(payload),originals=originals))
    try:
        for name in sorted(payload,key=lambda name:name=='data/version.json'):
            content,mode=payload[name];target=destination(root,name)
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as stream:
                temporary=Path(stream.name);stream.write(content)
            try:
                if mode:temporary.chmod(mode)
                os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
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


def auto_update(root=ROOT, check_only=False):
    recover(root)
    # Source checkouts are maintained through Git, never overwritten by the player updater.
    if (root/'.git').exists():return 'Git checkout; automatic player updates skipped.'
    preference=root/'userdata/update-settings.json'
    if preference.is_file() and json.loads(preference.read_text()).get('enabled') is False:
        return 'Automatic updates disabled / 已关闭自动更新'
    info=manifest()
    if info['sequence']<=version(root).get('sequence',0):return 'Already up to date / 已是最新版'
    if version(root).get('sequence',0)<info.get('minimum_sequence',0):
        raise ValueError('Download the complete player package first / 请先下载新版完整玩家包')
    if check_only:return 'Update available / 可更新: '+info['version']
    data=fetch(info['file'],MAX_DOWNLOAD)
    apply_update(data,info,root)
    return 'Updated / 已更新: '+info['version']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    options=parser.parse_args()
    with application_lock():print(auto_update(check_only=options.check))

if __name__=='__main__':main()
