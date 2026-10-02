"""Conservative static sanity check of a .safetensors CONTAINER (not of model compatibility).

Format (huggingface/safetensors): 8-byte little-endian u64 N, then N bytes of UTF-8 JSON object header (may be
right-padded with spaces), then the byte buffer. Each non-"__metadata__" entry is
{"dtype": str, "shape": [int>=0...], "data_offsets": [begin, end]} relative to the buffer start.

Passing means SAFETENSORS_CONTAINER_VALID only. Whether vLLM/PEFT accepts the adapter for Gemma 4 is H24/H25 and
harness-dependent (H23); nothing here claims that.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from tools.common import open_nofollow

MAX_HEADER = 100 * 1024 * 1024  # same ceiling the reference implementation uses
DTYPE_BYTES = {"BOOL": 1, "U8": 1, "I8": 1, "F8_E5M2": 1, "F8_E4M3": 1, "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
               "I32": 4, "U32": 4, "F32": 4, "I64": 8, "U64": 8, "F64": 8}


def check(path: Path) -> list[str]:
    """Return problems (empty list = SAFETENSORS_CONTAINER_VALID)."""
    try:
        with open_nofollow(path) as f:
            size = os.fstat(f.fileno()).st_size
            if size < 8:
                return [f"file is {size} bytes; shorter than the 8-byte header length"]
            n = int.from_bytes(f.read(8), "little")
            if n < 2 or n > MAX_HEADER or 8 + n > size:
                return [f"header length {n} is out of bounds for a {size}-byte file"]
            raw = f.read(n)
    except OSError as e:
        return [f"unreadable: {type(e).__name__}"]
    try:
        header = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ["header is not valid UTF-8 JSON"]
    if not isinstance(header, dict):
        return ["header JSON is not an object"]
    buffer_len = size - 8 - n
    problems, spans = [], []
    meta = header.get("__metadata__")
    if meta is not None and not (isinstance(meta, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in meta.items())):
        problems.append("__metadata__ must be a str->str object")
    for name, t in header.items():
        if name == "__metadata__":
            continue
        if not isinstance(t, dict):
            problems.append(f"{name}: entry is not an object")
            continue
        dtype, shape, off = t.get("dtype"), t.get("shape"), t.get("data_offsets")
        if not isinstance(dtype, str):
            problems.append(f"{name}: dtype missing")
        if not (isinstance(shape, list) and all(isinstance(d, int) and not isinstance(d, bool) and d >= 0 for d in shape)):
            problems.append(f"{name}: shape must be a list of non-negative ints")
            continue
        if not (isinstance(off, list) and len(off) == 2 and all(isinstance(o, int) and not isinstance(o, bool) for o in off)):
            problems.append(f"{name}: data_offsets must be [begin, end] ints")
            continue
        b, e = off
        if not (0 <= b <= e <= buffer_len):
            problems.append(f"{name}: data_offsets {off} outside buffer of {buffer_len} bytes")
            continue
        if dtype in DTYPE_BYTES and e - b != math.prod(shape) * DTYPE_BYTES[dtype]:
            problems.append(f"{name}: {e - b} bytes != shape {shape} x {dtype}")
        spans.append((b, e, name))
    spans.sort()
    for (b1, e1, n1), (b2, e2, n2) in zip(spans, spans[1:]):
        if b2 < e1:
            problems.append(f"{n1} and {n2} overlap")
    return problems
