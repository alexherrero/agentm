#!/usr/bin/env python3
# embed.py — text embedding for MemoryVault entries via sentence-transformers.
#
# v0.9.2 (2026-05-20): API mode dropped. Local sentence-transformers is the
# only production mode; stub mode remains for tests. See ADR 0001's
# 2026-05-20 amendment for the rationale (operator-config-assumption shift
# to desktop-class hardware + Claude-Ultra-subscriber-without-API-key
# personal-dev-env framing).
#
# Two modes:
#   - "local" — sentence-transformers via Python. Default. Requires the
#               `sentence-transformers` pip package (hard dep as of
#               v0.9.2; install scripts pull it automatically). Lazy-
#               loads `BAAI/bge-large-en-v1.5` on first use (~1.3GB on
#               disk + ~1.5GB RAM at runtime; Apple Silicon uses PyTorch
#               MPS automatically).
#   - "stub"  — deterministic 1024-d hash-based vector. ONLY for testing;
#               NEVER used in production. Smoke install + unit fixtures
#               use stub mode to validate wiring without network or model
#               download cost.
#
# Mode resolution:
#   1. Explicit --mode arg (CLI) or `mode=` kwarg (Python).
#   2. Default: "local".
#
# Model override (escape hatch for operators with low-spec hosts or model
# preferences — see locked design call L11 in plan #18):
#   - AGENT_TOOLKIT_EMBEDDING_MODEL env var sets the local model. Operators
#     are responsible for picking one with EMBEDDING_DIM-d native output.
#   - Default: BAAI/bge-large-en-v1.5 (1024-d).
#
# Graceful-skip behavior (per parent design's Tech Debt #1):
#   - If local mode + no sentence-transformers → raise EmbeddingUnavailable.
#   - Callers (save.py / evolve.py async path) catch EmbeddingUnavailable
#     + log warning + leave the queue entry pending. File write is never
#     blocked.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Local sentence-transformers model (locked design calls L1 + L11, plan #18
# task 1). BGE-large produces native 1024-d output; near-SOTA MTEB English
# (64.2); fast on Apple Silicon via PyTorch MPS backend.
_DEFAULT_LOCAL_MODEL = "BAAI/bge-large-en-v1.5"

# Local model cache directory. sentence-transformers respects the
# SENTENCE_TRANSFORMERS_HOME env var for its model cache location. We pin
# it to ~/.cache/crickets/sentence-transformers/ so:
#   1. The download stays under a single toolkit-owned directory (operators
#      can rm -rf the cache cleanly to free disk space).
#   2. It doesn't conflict with other tools that use sentence-transformers
#      via the package's default cache location (~/.cache/huggingface/ or
#      ~/.cache/torch/sentence_transformers/).
#   3. The cache is durable across MemoryVault sessions + survives toolkit
#      reinstalls (the toolkit's installer never touches user home dirs
#      outside .claude/ and .agents/).
# Set lazily inside _embed_local() so importing this module doesn't touch
# os.environ for callers that only use stub mode.
_LOCAL_CACHE_DIR = Path(
    os.environ.get(
        "AGENT_TOOLKIT_SENTENCE_TRANSFORMERS_CACHE",
        str(Path.home() / ".cache" / "crickets" / "sentence-transformers"),
    )
).expanduser()

# Embedding dimension. BGE-large native = 1024. Bumped from 384 (v1
# all-MiniLM-L6-v2 + truncated Voyage) in v0.9.2 alongside the local-only
# refactor — see ADR 0001's 2026-05-20 amendment for rationale + plan #18.
# Operators using AGENT_TOOLKIT_EMBEDDING_MODEL to swap models are
# responsible for picking one with 1024-d native output (or for accepting
# the vec-index dim mismatch which triggers a graceful rebuild-required
# error).
EMBEDDING_DIM = 1024


class EmbeddingUnavailable(Exception):
    """Raised when local mode can't be served (sentence-transformers
    not installed, or model load failed). Callers should catch + log +
    leave queue entry pending; file write is never blocked."""


def _resolve_mode(arg_mode: str | None) -> str:
    """Resolve embedding mode. Default = local.

    Raises ValueError for unknown modes. The previously-supported "api"
    mode was removed in v0.9.2 (see ADR 0001's 2026-05-20 amendment);
    "api" produces a clear error pointing operators at the release
    notes.
    """
    if arg_mode:
        if arg_mode == "api":
            raise ValueError(
                "API embedding mode was removed in crickets v0.9.2. "
                "Use --mode local (default) for sentence-transformers, or "
                "--mode stub for tests. See ADR 0001's 2026-05-20 "
                "amendment + plan #18 for rationale."
            )
        if arg_mode not in {"local", "stub"}:
            raise ValueError(
                f"unknown mode {arg_mode!r}: expected local or stub"
            )
        return arg_mode
    return "local"


def _resolve_model() -> str:
    """Resolve which local model to load. Default = BAAI/bge-large-en-v1.5;
    operators can override via AGENT_TOOLKIT_EMBEDDING_MODEL env var.

    Operators picking a non-default model must ensure it produces
    EMBEDDING_DIM-d output; consumers reject vectors whose
    dimension mismatches with a clear error.
    """
    return os.environ.get("AGENT_TOOLKIT_EMBEDDING_MODEL", _DEFAULT_LOCAL_MODEL)


def _embed_local(text: str) -> list[float]:
    """Embed via sentence-transformers (1024-d native via BGE-large default;
    custom model via AGENT_TOOLKIT_EMBEDDING_MODEL override).

    Lazy-imports sentence-transformers — though it's a hard install dep
    as of v0.9.2, lazy import keeps cold-start fast for callers using
    only stub mode (e.g. CI smoke tests).

    Sets SENTENCE_TRANSFORMERS_HOME to _LOCAL_CACHE_DIR before importing
    so the model checkpoint lands under
    ~/.cache/crickets/sentence-transformers/ (plan #7a part 2 task 4
    locked path; durable across toolkit reinstalls).
    """
    # Pin the cache directory before sentence-transformers imports + reads
    # SENTENCE_TRANSFORMERS_HOME at module-init time. Idempotent — re-
    # running always points at the same location.
    _LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(_LOCAL_CACHE_DIR))
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as e:
        raise EmbeddingUnavailable(
            "Local mode requires the `sentence-transformers` Python "
            "package. Install via: pip install sentence-transformers"
        ) from e
    # Lazy-load the model on first call; cache instances keyed by name so
    # AGENT_TOOLKIT_EMBEDDING_MODEL override produces a distinct cached
    # instance on first reference (rather than clobbering the default).
    model_name = _resolve_model()
    global _LOCAL_MODEL_INSTANCES
    try:
        _LOCAL_MODEL_INSTANCES  # noqa: F821
    except NameError:
        _LOCAL_MODEL_INSTANCES = {}
    if model_name not in _LOCAL_MODEL_INSTANCES:
        _LOCAL_MODEL_INSTANCES[model_name] = SentenceTransformer(model_name)
    model = _LOCAL_MODEL_INSTANCES[model_name]
    embedding = model.encode([text])[0].tolist()
    return list(embedding)


def _embed_stub(text: str) -> list[float]:
    """Deterministic hash-based fake embedding. Testing only."""
    # SHA-256 of text -> 32 bytes -> repeat to fill EMBEDDING_DIM floats.
    # Each byte normalized to [-1, 1].
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    i = 0
    while len(out) < EMBEDDING_DIM:
        b = digest[i % len(digest)]
        out.append((b - 128) / 128.0)
        i += 1
    return out


def embed_text(text: str, *, mode: str | None = None) -> list[float]:
    """Embed text via the configured mode. Returns EMBEDDING_DIM-length floats.

    Raises:
        EmbeddingUnavailable: if local mode can't be served
            (sentence-transformers missing).
        ValueError: if mode is not local or stub. (API mode was removed
            in v0.9.2; see ADR 0001's 2026-05-20 amendment.)
    """
    resolved = _resolve_mode(mode)
    if resolved == "local":
        return _embed_local(text)
    if resolved == "stub":
        return _embed_stub(text)
    raise ValueError(f"internal: unhandled mode {resolved!r}")  # pragma: no cover


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-embed",
        description=(
            "Embed text via local sentence-transformers (default) or stub. "
            "API mode was removed in v0.9.2; see ADR 0001's 2026-05-20 "
            "amendment for rationale."
        ),
    )
    parser.add_argument("text", help="text to embed (or '-' to read from stdin)")
    parser.add_argument(
        "--mode",
        default=None,
        # No choices= constraint so "api" reaches _resolve_mode + gets the
        # informative error message rather than argparse's terse "invalid
        # choice" output.
        help="embedding mode: local (default) or stub",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    text = sys.stdin.read() if args.text == "-" else args.text
    try:
        embedding = embed_text(text, mode=args.mode)
    except EmbeddingUnavailable as e:
        print(f"EMBEDDING_UNAVAILABLE: {e}", file=sys.stderr)
        return 2  # Distinct exit code so callers can detect graceful-skip.
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    # Stdout: JSON-encoded list of floats (script-pipeable).
    print(json.dumps(embedding))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
