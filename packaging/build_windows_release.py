"""Build a player ZIP with a portable Windows Python/Tk runtime.

Run with the matching full CPython installation on Windows. No installed
packages, machine configuration, user profiles or personal paths are copied.
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

VERSION = '3.12.10'
EMBED_URL = 'https://www.python.org/ftp/python/' + VERSION + '/python-' + VERSION + '-embed-amd64.zip'
EMBED_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
ROOT = Path(__file__).resolve().parents[1]


def download(url, digest=None):
    with urlopen(url, timeout=60) as response:
        data = response.read()
    if digest and hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError('Download checksum mismatch: ' + url)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
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


if __name__ == '__main__':
    main()
