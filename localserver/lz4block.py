"""Pure-Python LZ4 block decoder.

The retail ``CreateWorld`` payload is ``[u32 BE raw size][LZ4 block]``.  The
packaged runtime does not ship the ``lz4`` C module, so the room loader needs
a decoder that handles real blocks (literals + matches), not only the
literals-only blocks our own encoder emits.

Target runtime: CPython 3.5+ / PyPy3 (no f-strings).
"""

def decompress(data, expected_size=None):
    """Decode one raw LZ4 block.

    ``data`` starts at the block token; the frame header and the u32 size
    prefix are the caller's business.  Raises ValueError on a malformed
    block or when the decoded size does not match ``expected_size``.
    """
    out = bytearray()
    index = 0
    total = len(data)
    while index < total:
        token = data[index]
        index += 1
        literals = token >> 4
        if literals == 15:
            while True:
                if index >= total:
                    raise ValueError('lz4: truncated literal length')
                extra = data[index]
                index += 1
                literals += extra
                if extra != 255:
                    break
        if literals:
            chunk = data[index:index + literals]
            if len(chunk) != literals:
                raise ValueError('lz4: truncated literals')
            out += chunk
            index += literals
        if index >= total:
            break
        if index + 2 > total:
            raise ValueError('lz4: truncated match offset')
        offset = data[index] | (data[index + 1] << 8)
        index += 2
        match = token & 15
        if match == 15:
            while True:
                if index >= total:
                    raise ValueError('lz4: truncated match length')
                extra = data[index]
                index += 1
                match += extra
                if extra != 255:
                    break
        match += 4
        start = len(out) - offset
        if start < 0:
            raise ValueError('lz4: negative match offset')
        for position in range(match):
            out.append(out[start + position])
    if expected_size is not None and len(out) != expected_size:
        raise ValueError('lz4: expected %d bytes, got %d'
                         % (expected_size, len(out)))
    return bytes(out)
