"""Names and dimensions of original PNG atlases; never user texture content."""
from pathlib import Path
import struct


def catalogue(directory):
    root=Path(directory)
    rows=[]
    for file in sorted(root.rglob('*.png')):
        with file.open('rb') as source:header=source.read(24)
        if len(header)!=24 or header[:8]!=b'\x89PNG\r\n\x1a\n' or header[12:16]!=b'IHDR':continue
        width,height=struct.unpack('>II',header[16:24])
        rows.append(dict(path='/'+file.relative_to(root).as_posix(),width=width,height=height))
    return rows
