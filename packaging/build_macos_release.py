"""Package the local browser service with an official standalone CPython runtime."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile

from runtime_dependencies import install

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = {
    'arm64': ('aarch64', '9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de'),
    'x64': ('x86_64', 'd09998f85752b646873a8a6bc7eea442a0d251b1c8d2413aa8ea06ef131c3335'),
}
LICENSES = ('LICENSE', 'LICENSE.bdb.txt', 'LICENSE.bzip2.txt', 'LICENSE.cpython.txt',
    'LICENSE.expat.txt', 'LICENSE.libX11.txt', 'LICENSE.libXau.txt', 'LICENSE.libedit.txt',
    'LICENSE.libffi.txt', 'LICENSE.liblzma.txt', 'LICENSE.libuuid.txt', 'LICENSE.libxcb.txt',
    'LICENSE.mpdecimal.txt', 'LICENSE.ncurses.txt', 'LICENSE.openssl-1.1.txt',
    'LICENSE.openssl-3.txt', 'LICENSE.sqlite.txt', 'LICENSE.tcl.txt', 'LICENSE.tix.txt', 'LICENSE.zlib.txt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=RUNTIMES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-archive', type=Path)
    args = parser.parse_args()
    arch, digest = RUNTIMES[args.arch]
    url = ('https://github.com/astral-sh/python-build-standalone/releases/download/20261013/'
           'cpython-3.12.15%2B20261013-' + arch + '-apple-darwin-install_only_stripped.tar.gz')
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        runtime = args.runtime_archive
        if runtime is None:
            runtime = staging / 'python.tar.gz'
            with urlopen(url, timeout=120) as response:
                runtime.write_bytes(response.read())
        if hashlib.sha256(runtime.read_bytes()).hexdigest() != digest:
            raise SystemExit('Standalone Python checksum mismatch')
        source = staging / 'source.zip'
        subprocess.run(['git', 'archive', '--format=zip', '--prefix=starbreak-singleplayer/',
                        '-o', str(source), 'HEAD'], cwd=ROOT, check=True)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(staging)
        project = staging / 'starbreak-singleplayer'
        with tarfile.open(runtime) as archive:
            archive.extractall(project, filter='data')
        python = project / 'python'
        # Keep the executable runtime, not SDK headers or package-development tools.
        for path in (python / 'include', python / 'share', python / 'lib/pkgconfig',
                     python / 'lib/python3.12/ensurepip', python / 'lib/python3.12/site-packages/pip'):
            if path.exists():
                shutil.rmtree(path)
        for pattern in ('lib/*.a', 'lib/python3.12/config-*', 'lib/python3.12/site-packages/pip-*.dist-info'):
            for path in python.glob(pattern):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        for path in (python / 'bin').iterdir():
            if path.name not in ('python', 'python3', 'python3.12'):
                path.unlink()
        licenses = python / 'licenses'
        licenses.mkdir()
        def license_file(name):
            with urlopen('https://raw.githubusercontent.com/astral-sh/python-build-standalone/20261013/' + name, timeout=60) as response:
                (licenses / name).write_bytes(response.read())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(license_file, LICENSES))
        site = python / 'lib/python3.12/site-packages'
        install(site)
        (python / 'SOURCE.txt').write_text('CPython 3.12.15, python-build-standalone 20261013\n'
            + url + '\nSHA256: ' + digest + '\nLicenses included under python/licenses.\n', encoding='utf8')
        (project / 'start-web.command').chmod(0o755)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for path in sorted(project.rglob('*')):
                if path.is_file():
                    output.write(path, path.relative_to(staging))
    print(args.output)
    print('SHA256: ' + hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
