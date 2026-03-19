"""SHA-256 file hashing for deduplication.

Matches the behaviour of the legacy hash.ts:
    createHash('sha256').update(buffer).digest('hex')
"""

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    """Return the lowercase hex SHA-256 digest of a file, streaming in 64 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
