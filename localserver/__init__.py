"""Fully local StarBreak server lab.

This package speaks the retail client's real wire protocol:

    frame = type (1 byte) + payload length (int32 LE) + payload

The first frame (MT_HELLO) is plaintext.  The client's MT_CONNECT carries
the 32-bit seed used by its PRNG; both sides derive the same 64-byte RC4 key
from it, and every later frame is RC4-encrypted as one continuous stream in
each direction.
"""
