"""Personal Access Token validation.

code-smriti does not mint PATs. They are created in the Chief of Staff
web UI (API Tokens panel) and stored as ``api_token`` documents in the
shared ``chief_of_staff`` bucket. CoS and code-smriti run against the
same Couchbase cluster, so a PAT minted in CoS is a valid Bearer
credential here too.

This module is validation-only — minting and revocation live in
chief-of-staff. Only the SHA-256 hash of a token is ever persisted.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from couchbase.options import QueryOptions
from loguru import logger

from ..config import settings
from ..database import get_cluster

PAT_PREFIX = "cos_pat_"
DOC_TYPE = "api_token"


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a raw token — only the hash is ever stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_api_token_by_hash(token_hash: str) -> Optional[dict]:
    """Look up a non-revoked PAT by its hash in the shared CoS store.

    Returns ``{"id", "user_id"}`` for a live token, ``None`` if the
    token is unknown or has been revoked.
    """
    bucket = settings.couchbase_bucket_cos
    fqn = f"`{bucket}`.`_default`.`documents`"
    query = (
        f"SELECT t.id, t.user_id, t.revoked_at "
        f"FROM {fqn} t WHERE t.type = $type AND t.token_hash = $hash "
        f"LIMIT 1"
    )
    rows = list(
        get_cluster().query(
            query,
            QueryOptions(named_parameters={"type": DOC_TYPE, "hash": token_hash}),
        )
    )
    if not rows:
        return None
    row = rows[0]
    if row.get("revoked_at"):
        return None
    return {"id": row["id"], "user_id": row["user_id"]}


def touch_api_token(token_id: str) -> None:
    """Best-effort ``last_used_at`` update. Never raises — auth must not
    depend on this write succeeding."""
    bucket = settings.couchbase_bucket_cos
    doc_id = f"api_token::{token_id}"
    try:
        collection = (
            get_cluster()
            .bucket(bucket)
            .scope("_default")
            .collection("documents")
        )
        doc = collection.get(doc_id).content_as[dict]
        doc["last_used_at"] = datetime.now(timezone.utc).isoformat()
        collection.replace(doc_id, doc)
    except Exception as e:
        logger.debug(f"PAT touch failed for {token_id}: {e}")
