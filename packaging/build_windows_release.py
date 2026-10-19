"""Build a player ZIP with a portable Windows Python/Tk runtime.

Run with the matching full CPython installation on Windows, or reuse a
portable CPython 3.12/Tk distribution with --runtime-from. The source tree is
always taken from Git HEAD, excluding untracked settings and development files.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import zipfile

from runtime_dependencies import install, verify_sqlite

VERSION = '3.12.10'
EMBED_URL = 'https://www.python.org/ftp/python/' + VERSION + '/python-' + VERSION + '-embed-amd64.zip'
EMBED_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def install_viewer(runtime):
    from tools.desktop_view import install as viewer
    directory=runtime/'desktop-view'
    viewer(directory,target='windows-x64',check_python=False,bundled=False)
    for cache in directory.glob('*.download'):cache.unlink()


def download(url, digest=None):
    with urlopen(url, timeout=60) as response:
        data = response.read()
    if digest and hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError('Download checksum mismatch: ' + url)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-from', type=Path,
                        help='Reuse a portable CPython 3.12/Tk runtime without launching a GUI.')
    args = parser.parse_args()
    if args.runtime_from:
        build_with_runtime(args.runtime_from.resolve(), args.output.resolve())
        return
    if os.name != 'nt' or sys.version.split()[0] != VERSION:
        raise SystemExit('Build using Windows x64 CPython ' + VERSION + ' with Tk.')
    import tkinter as tk
    window = tk.Tk()
    tcl_version = window.tk.call('info', 'patchlevel')
    tk_version = window.tk.call('package', 'provide', 'Tk')
    window.destroy()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        source_zip = staging / 'source.zip'
        subprocess.run(['git', 'archive', '--format=zip', '--prefix=starbreak-singleplayer/',
                        '-o', str(source_zip), 'HEAD'], cwd=ROOT, check=True)
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(staging)
        project = staging / 'starbreak-singleplayer'
        runtime = project / 'python'
        runtime.mkdir()
        embed_zip = staging / 'embed.zip'
        embed_zip.write_bytes(download(EMBED_URL, EMBED_SHA256))
        with zipfile.ZipFile(embed_zip) as archive:
            archive.extractall(runtime)
        installed = Path(sys.base_prefix)
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'test', 'tests', 'demos')
        shutil.copytree(installed / 'Lib/tkinter', runtime / 'tkinter', ignore=ignore)
        shutil.copytree(installed / 'tcl', runtime / 'tcl', ignore=ignore)
        for name in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', 'zlib1.dll'):
            shutil.copy2(installed / 'DLLs' / name, runtime / name)
        for name, version in (('tcl', tcl_version), ('tk', tk_version)):
            tag = 'core-' + version.replace('.', '-')
            url = 'https://raw.githubusercontent.com/tcltk/' + name + '/' + tag + '/license.terms'
            (runtime / (name.upper() + '-LICENSE.txt')).write_bytes(download(url))
        install(runtime)
        install_viewer(runtime)
        verify_sqlite(runtime, windows=True)
        (runtime / 'python312._pth').write_text('python312.zip\n.\n..\n../tools\n', encoding='utf8')
        (runtime / 'SOURCE.txt').write_text(
            'CPython ' + VERSION + ' Windows x64 embeddable distribution\n'
            + EMBED_URL + '\nSHA256: ' + EMBED_SHA256 + '\n'
            + 'Tkinter and Tcl/Tk from the matching CPython full installation.\n'
            + 'Tcl ' + tcl_version + '; Tk ' + tk_version + '\n'
            + 'Licenses: LICENSE.txt, TCL-LICENSE.txt, TK-LICENSE.txt.\n'
            + 'python312._pth is configured for this portable player package.\n', encoding='utf8')
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(project.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(staging))
    print(output)
    print('SHA256: ' + hashlib.sha256(output.read_bytes()).hexdigest())


def build_with_runtime(source, output):
    required = ('python.exe', 'pythonw.exe', 'python312.dll', 'python312.zip',
                '_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', 'zlib1.dll',
                'LICENSE.txt', 'tkinter', 'tcl/tcl8.6', 'tcl/tk8.6')
    missing = [name for name in required if not (source / name).exists()]
    if missing:
        raise SystemExit('Portable runtime missing: ' + ', '.join(missing))
    verify_sqlite(source, windows=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        source_zip = staging / 'source.zip'
        subprocess.run(['git', 'archive', '--format=zip', '--prefix=starbreak-singleplayer/',
                        '-o', str(source_zip), 'HEAD'], cwd=ROOT, check=True)
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(staging)
        project = staging / 'starbreak-singleplayer'
        runtime = project / 'python'
        shutil.copytree(source, runtime,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log'))
        install(runtime)
        install_viewer(runtime)
        (runtime / 'python312._pth').write_text(
            'python312.zip\n.\n..\n../tools\n', encoding='utf8')
        if not (runtime / 'SOURCE.txt').exists():
            (runtime / 'SOURCE.txt').write_text(
                'Portable CPython 3.12 with matching Tkinter and Tcl/Tk.\n'
                'Runtime retained from the local singleplayer distribution.\n'
                'python312._pth is configured for this portable player package.\n', encoding='utf8')
        for name in ('tcl', 'tk'):
            license_file = runtime / (name.upper() + '-LICENSE.txt')
            if not license_file.exists():
                candidates = list((runtime / 'tcl').glob(name + '*/license.terms'))
                if not candidates:
                    raise SystemExit('Supply the runtime ' + name.upper() + '-LICENSE.txt before packaging.')
                shutil.copy2(candidates[0], license_file)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(project.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(staging))
    print(output)
    print('SHA256: ' + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
