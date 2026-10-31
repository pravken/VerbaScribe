"""
Signatures of releases: SHA256SUMS.txt is signed with the author's key, the updater checks the
signature with the public key built into the program (app/updater.py RELEASE_PUBLIC_KEY).

The checksum alone only says that a file arrived as GitHub serves it; whoever can publish a release
on the repository can also publish matching checksums. The signature needs the private key, which
only the author keeps (a secret of the release workflow, created by tools/release_key.py).

The files are in minisign's format (https://jedisct1.github.io/minisign/), so anyone can check a
release by hand: minisign -Vm SHA256SUMS.txt -P <public key>. The trusted comment holds the tag of
the release: a signature of one release does not pass for another.

Ed25519 is written out here (RFC 8032) in plain Python: one check per update, no new library.
"""

import base64
import hashlib
import os
from typing import Optional, Tuple

# ------------------------------------------------------------------ Ed25519 (RFC 8032, section 5.1)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    """Points in extended coordinates (X, Y, Z, T)."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = 2 * t1 * t2 * _D % _P
    D = 2 * z1 * z2 % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)  # the neutral point
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def _compress(p) -> bytes:
    zinv = pow(p[2], _P - 2, _P)
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _secret_scalar(seed: bytes) -> Tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    return _compress(_mul(_secret_scalar(seed)[0], _G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_scalar(seed)
    pk = _compress(_mul(a, _G))
    r = _h(prefix + msg) % _L
    R = _compress(_mul(r, _G))
    s = (r + _h(R + pk + msg) * a) % _L
    return R + int.to_bytes(s, 32, "little")


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pk) != 32 or len(sig) != 64:
        return False
    A = _decompress(pk)
    R = _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    k = _h(sig[:32] + pk + msg) % _L
    return _equal(_mul(s, _G), _add(R, _mul(k, A)))


# ------------------------------------------------------------------ minisign files
class SignatureError(ValueError):
    pass


def _key_parts(public_key_text: str) -> Tuple[bytes, bytes]:
    """(key id, 32-byte key) of a minisign public key (the base64 line, a comment line before it allowed)."""
    line = [x for x in public_key_text.strip().splitlines() if x and not x.startswith("untrusted comment:")][-1]
    raw = base64.b64decode(line.strip())
    if len(raw) != 42 or raw[:2] != b"Ed":
        raise SignatureError("not a minisign Ed25519 public key")
    return raw[2:10], raw[10:]


def public_key_text(seed: bytes, key_id: bytes) -> str:
    return base64.b64encode(b"Ed" + key_id + public_key(seed)).decode()


def sign_file(seed: bytes, key_id: bytes, data: bytes, trusted_comment: str) -> str:
    """The text of a .minisig file for `data` (prehashed with BLAKE2b-512, as minisign -H)."""
    if "\n" in trusted_comment:
        raise ValueError("one line of trusted comment")
    sig = sign(seed, hashlib.blake2b(data, digest_size=64).digest())
    glob = sign(seed, sig + trusted_comment.encode())
    return (f"untrusted comment: signature from Verbaliox release key\n"
            f"{base64.b64encode(b'ED' + key_id + sig).decode()}\n"
            f"trusted comment: {trusted_comment}\n"
            f"{base64.b64encode(glob).decode()}\n")


def verify_file(public_key_text: str, data: bytes, minisig_text: str) -> str:
    """The trusted comment when `minisig_text` is a valid signature of `data` by the key; else
    SignatureError."""
    key_id, pk = _key_parts(public_key_text)
    lines = [x.rstrip("\r") for x in minisig_text.strip().splitlines()]
    if len(lines) != 4 or not lines[0].startswith("untrusted comment:") or not lines[2].startswith("trusted comment: "):
        raise SignatureError("not a minisign signature")
    try:
        raw, glob = base64.b64decode(lines[1]), base64.b64decode(lines[3])
    except ValueError:
        raise SignatureError("damaged signature")
    if len(raw) != 74 or len(glob) != 64:
        raise SignatureError("damaged signature")
    alg, sig_key, sig = raw[:2], raw[2:10], raw[10:]
    if sig_key != key_id:
        raise SignatureError("signed with another key")
    if alg == b"ED":
        msg = hashlib.blake2b(data, digest_size=64).digest()
    elif alg == b"Ed":
        msg = data
    else:
        raise SignatureError("unknown signature algorithm")
    if not verify(pk, msg, sig):
        raise SignatureError("the file does not match its signature")
    comment = lines[2][len("trusted comment: "):]
    if not verify(pk, sig + comment.encode(), glob):
        raise SignatureError("the trusted comment does not match the signature")
    return comment


def new_key() -> Tuple[bytes, bytes]:
    """(seed, key id): a new signing key."""
    return os.urandom(32), os.urandom(8)
