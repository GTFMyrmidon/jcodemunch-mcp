"""Evidence: what the server can attest, and where that attestation comes from.

Two independent halves, deliberately not coupled:

* ``receipts`` + ``producers`` (#377 phase 2) — exact immutable evidence receipts
  (``jcodemunch.evidence/v1``) served at ``munch://evidence/<id>``, plus the
  producer registration that decides who may mint one. Imported lazily by the
  server, so they are not re-exported here.
* ``scip`` + ``scip_ingest`` — compile-time reference edges ingested from a SCIP
  index, re-exported below.

The compile-time sibling of the ``runtime/`` trace-ingestion package:
``import-scip`` parses a SCIP index file (the protobuf artifact emitted by
scip-typescript, scip-python, scip-java, scip-go, rust-analyzer, scip-clang)
and stores compiler-verified reference/implementation edges in the repo's
``scip_*`` tables. Read-only with respect to the user's code; the only write
is to the per-repo index database.
"""

from .scip import (
    ScipDocument,
    ScipIndex,
    ScipOccurrence,
    ScipRelationship,
    ScipSymbolInfo,
    display_name_from_symbol,
    is_local_symbol,
    parse_scip_bytes,
    parse_scip_file,
)
from .scip_ingest import ingest_scip_file

__all__ = [
    "ScipDocument",
    "ScipIndex",
    "ScipOccurrence",
    "ScipRelationship",
    "ScipSymbolInfo",
    "display_name_from_symbol",
    "ingest_scip_file",
    "is_local_symbol",
    "parse_scip_bytes",
    "parse_scip_file",
]
