"""Build the small source-only update; upload latest.json only after the ZIP."""
import hashlib
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from update import managed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',help='Cumulative update from an installed bootstrap commit/ref')
    options=parser.parse_args()
    selected=None;base_version=None
    if options.base:
        base_version=json.loads(subprocess.check_output(['git','show',options.base+':data/version.json'],cwd=ROOT))
        selected=set(subprocess.check_output(['git','diff','--name-only','--diff-filter=ACMRT',options.base,'HEAD'],cwd=ROOT,text=True).splitlines())
        selected.update(('data/version.json','tools/launch.py','tools/update.py','tools/web_app.py'))
    source=zipfile.ZipFile(io.BytesIO(subprocess.check_output(['git','archive','--format=zip','--prefix=starbreak-singleplayer/','HEAD'],cwd=ROOT)))
    metadata=json.loads(source.read('starbreak-singleplayer/data/version.json'))
    directory=ROOT/'dist/updates';directory.mkdir(parents=True,exist_ok=True)
    name='starbreak-singleplayer-update-'+metadata['version']+'.zip'
    output=directory/name
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for item in source.infolist():
            if item.is_dir():continue
            relative=item.filename[len('starbreak-singleplayer/'):]
            if selected is not None and relative not in selected:continue
            try:managed(relative)
            except ValueError:continue
            archive.writestr(item,source.read(item))
    data=output.read_bytes()
    manifest=dict(schema=1,version=metadata['version'],sequence=metadata['sequence'],file=name,
                  size=len(data),sha256=hashlib.sha256(data).hexdigest())
    if base_version:manifest['minimum_sequence']=base_version['sequence']
    (directory/'latest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
    print(json.dumps(manifest))

if __name__=='__main__':main()
