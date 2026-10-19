"""Do not ship a runtime that can open the editor but cannot save battles."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('portable_runtime_dependencies',
    Path(__file__).resolve().parents[1]/'packaging/runtime_dependencies.py')
dependencies=importlib.util.module_from_spec(spec);spec.loader.exec_module(dependencies)


class PortableSqliteTests(unittest.TestCase):
    def test_windows_requires_native_libraries_and_both_standard_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('_sqlite3.pyd','sqlite3.dll'):(root/name).write_bytes(b'test native library')
            with zipfile.ZipFile(root/'python312.zip','w') as archive:
                archive.writestr('sqlite3/__init__.pyc',b'test stdlib')
            with self.assertRaisesRegex(RuntimeError,'standard library'):dependencies.verify_sqlite(root,windows=True)
            with zipfile.ZipFile(root/'python312.zip','a') as archive:
                archive.writestr('sqlite3/dbapi2.pyc',b'test stdlib')
            dependencies.verify_sqlite(root,windows=True)
            (root/'sqlite3.dll').unlink()
            with self.assertRaisesRegex(RuntimeError,'sqlite3.dll'):dependencies.verify_sqlite(root,windows=True)

    def test_mac_requires_standard_library_and_native_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);library=root/'lib/python3.12'
            (library/'sqlite3').mkdir(parents=True);(library/'lib-dynload').mkdir()
            for name in ('__init__.py','dbapi2.py'):(library/'sqlite3'/name).write_text('# test stdlib')
            with self.assertRaisesRegex(RuntimeError,'standard library'):dependencies.verify_sqlite(root)
            (library/'lib-dynload/_sqlite3.cpython-312-darwin.so').write_bytes(b'test native extension')
            dependencies.verify_sqlite(root)
