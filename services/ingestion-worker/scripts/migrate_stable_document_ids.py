#!/usr/bin/env python3
"""
Move repo/module/file/symbol docs onto commit-independent identities, and split
LLM-chunker output out of the symbol table.

Background:
    Identity used to include the commit — `symbol:{repo}:{path}:{name}:{commit}`.
    That made re-ingestion an *insert* rather than an upsert: every full re-ingest
    minted a fresh document for every symbol and orphaned its predecessor, because
    only the incremental path ran a cleanup and that cleanup keyed on commit.
    `fema/LOMRs.py` ended up with 26 documents across 5 generations for its 12
    functions; 1,835 files corpus-wide carried more than one generation.

    Separately, the LLM chunker's output was appended to the tree-sitter symbol
    list and stored as `symbol_index`. Measured 2026-08-18: 31,749 of 93,994
    "symbols" (34%) carry a chunker-invented `symbol_type` — `workflow`,
    `integration`, `validation`, and a tail of ~110 one-off categories — rather
    than a grammar node kind. Their names are not identifiers and do not
    necessarily exist in the file: one test file was indexed under
    `RealstackAPI.get_users` / `get_cities`, the names of the client methods it
    *tests*.

    `v4/schemas.py` now derives identity from location alone and gives chunker
    regions their own `semantic_unit` type. This script moves the corpus over.

Why generations cannot simply be deduped:
    Neither the chunker's labels nor its spans are stable between runs — the same
    region of `fema/LOMRs.py` came back as `fema_api_response_validation` at lines
    40-58 on one run and `fema_response_validation` at 40-65 on the next. So there
    is no key under which the historical copies collapse.

    The sound rule is reconciliation against the current parse, not deduplication:
    for each file, the `file_index` records the commit at which that file was last
    processed. Children carrying that commit are what the file currently contains;
    everything older described a version that no longer exists. If the newest
    generation produced no chunker regions — because tree-sitter found enough
    symbols that time — then the file genuinely has none, and every historical
    chunker doc for it is dropped.

What it does, one repository at a time:
    1. Establish each file's current commit from its newest `file_index`.
    2. Keep only child docs from that generation; mark the rest for deletion.
    3. Route survivors by `symbol_type`: grammar node kinds stay `symbol_index`
       (keyed on name), chunker categories become `semantic_unit` (keyed on span,
       with `symbol_name` demoted to `label`).
    4. Copy survivors to their new keys, rewriting `parent_id` / `children_ids`
       through the old→new map.
    5. Delete every old key.

Safety:
    - Dry-run by default; pass --execute to write.
    - Idempotent — a doc already on its stable id is left alone, so an interrupted
      run can be repeated.
    - Writes the new document before deleting the old one. A crash leaves a
      duplicate, never a hole.
    - Never empties a file: if the current-generation filter would drop every
      child of a file, the newest generation present is kept instead. Guards
      against a missing or malformed `file_index`.
    - `--repo` restricts the run to one repository, for a rehearsal.
    - `--repair-refs` runs only the parent-link repair described below.

Parent links:
    `parent_id` is *derived* from a document's own location rather than mapped
    through old→new ids, because many parents point at generations that earlier
    cleanups already deleted and so appear in no map built over live documents.
    A child's parent is always `file:{repo}:{path}`, and a file's parent is
    always `module:{repo}:{dirname(path)}` — both computable without the referent
    existing. 9,830 file→module links were dangling for want of this.

Note:
    `commit_hash` is untouched. It stays a field recording when a version was
    processed — what the RAG layer displays and what incremental reads to decide
    whether work is needed.

Usage:
    ./.venv/bin/python scripts/migrate_stable_document_ids.py                  # dry-run, all repos
    ./.venv/bin/python scripts/migrate_stable_document_ids.py --repo kbhalerao/ssurgo
    ./.venv/bin/python scripts/migrate_stable_document_ids.py --execute
    ./.venv/bin/python scripts/migrate_stable_document_ids.py --repair-refs --execute
"""

import argparse
import posixpath
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import couchbase.subdocument as SD
from couchbase.options import QueryOptions
from loguru import logger

from storage.couchbase_client import CouchbaseClient
from v4.schemas import (
    make_repo_id, make_module_id, make_file_id, make_symbol_id,
    make_semantic_unit_id,
)

BUCKET = "code_kosha"
TYPES = ("repo_summary", "module_summary", "file_index", "symbol_index")

# Grammar node kinds emitted by the tree-sitter parsers. Anything else in
# symbol_type came from the LLM chunker's free-form category vocabulary.
PARSER_SYMBOL_TYPES = frozenset({
    "function", "method", "class", "arrow_function",
    "interface", "enum", "struct", "module", "extension",
})


def _created_at(doc: Dict) -> str:
    return ((doc.get("version") or {}).get("created_at")) or ""


def _rank(doc: Dict) -> Tuple[str, str]:
    """Newest first, ties broken on document_id so runs are reproducible."""
    return (_created_at(doc), doc.get("document_id") or "")


def is_parser_symbol(doc: Dict) -> bool:
    return (doc.get("symbol_type") or "") in PARSER_SYMBOL_TYPES


def fetch_rows(cb: CouchbaseClient, repo_id: str) -> List[Dict]:
    """Project only what identity, routing and ranking need — never whole docs."""
    query = f"""
        SELECT d.document_id, d.type, d.repo_id, d.file_path, d.module_path,
               d.symbol_name, d.symbol_type, d.commit_hash, d.version,
               d.metadata.start_line AS start_line, d.metadata.end_line AS end_line
        FROM `{BUCKET}` d
        WHERE d.repo_id = $repo_id AND d.type IN $types
    """
    result = cb.cluster.query(
        query,
        QueryOptions(named_parameters={"repo_id": repo_id, "types": list(TYPES)}),
    )
    return list(result)


def target_identity(doc: Dict) -> Optional[Tuple[str, str]]:
    """
    (new document type, new document_id) for a doc, or None if unidentifiable.
    """
    dtype = doc.get("type")
    repo_id = doc.get("repo_id")
    if not repo_id:
        return None

    if dtype == "repo_summary":
        return "repo_summary", make_repo_id(repo_id)

    if dtype == "module_summary":
        # "" is the repo root and is legitimate, so test for None not falsiness.
        module_path = doc.get("module_path")
        if module_path is None:
            return None
        return "module_summary", make_module_id(repo_id, module_path)

    if dtype == "file_index":
        file_path = doc.get("file_path")
        if not file_path:
            return None
        return "file_index", make_file_id(repo_id, file_path)

    if dtype == "symbol_index":
        file_path = doc.get("file_path")
        if not file_path:
            return None
        if is_parser_symbol(doc):
            name = doc.get("symbol_name")
            if not name:
                return None
            return "symbol_index", make_symbol_id(repo_id, file_path, name)
        start, end = doc.get("start_line"), doc.get("end_line")
        if start is None or end is None:
            return None
        return "semantic_unit", make_semantic_unit_id(repo_id, file_path, start, end)

    return None


def current_commit_per_file(rows: List[Dict]) -> Dict[str, str]:
    """Each file's current commit, from its newest file_index."""
    newest: Dict[str, Dict] = {}
    for r in rows:
        if r.get("type") != "file_index":
            continue
        path = r.get("file_path")
        if not path:
            continue
        if path not in newest or _rank(r) > _rank(newest[path]):
            newest[path] = r
    return {
        path: doc.get("commit_hash")
        for path, doc in newest.items()
        if doc.get("commit_hash")
    }


def select_live_children(rows: List[Dict], current: Dict[str, str]) -> Tuple[List[Dict], List[Dict]]:
    """
    Split symbol docs into (kept, superseded) by the file's current generation.

    Falls back to the newest generation actually present when the file_index
    commit matches nothing — a file must never be left with no children because
    its file_index was missing or disagreed with its symbols.
    """
    by_file: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        if r.get("type") == "symbol_index" and r.get("file_path"):
            by_file[r["file_path"]].append(r)

    kept: List[Dict] = []
    dropped: List[Dict] = []
    for path, docs in by_file.items():
        commit = current.get(path)
        live = [d for d in docs if commit and d.get("commit_hash") == commit]
        if not live:
            newest_stamp = max((_created_at(d) for d in docs), default="")
            live = [d for d in docs if _created_at(d) == newest_stamp]
        live_ids = {id(d) for d in live}
        kept.extend(live)
        dropped.extend(d for d in docs if id(d) not in live_ids)
    return kept, dropped


def plan_repo(rows: List[Dict]) -> Tuple[Dict[str, Tuple[str, str]], List[str], Dict[str, str], Counter]:
    """
    Returns (survivor old_id -> (new_type, new_id), ids to delete, ref map, stats).

    The reference map covers *every* old id, not just survivors: a file's
    `parent_id` routinely points at an older generation of its module, and that
    generation is about to be deleted. Every generation of a thing maps to the
    same stable id, so the complete map is what keeps the hierarchy resolvable.
    """
    stats = Counter()
    current = current_commit_per_file(rows)
    kept_symbols, superseded_symbols = select_live_children(rows, current)

    stats["symbol_docs_superseded"] = len(superseded_symbols)

    # Everything that is not a symbol doc passes through generation selection
    # untouched; repo/module/file dedupe by stable key below.
    considered = [r for r in rows if r.get("type") != "symbol_index"] + kept_symbols

    id_map: Dict[str, str] = {}
    by_new: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)

    for row in rows:
        ident = target_identity(row)
        if ident and row.get("document_id"):
            id_map[row["document_id"]] = ident[1]

    for row in considered:
        old_id = row.get("document_id")
        if not old_id:
            stats["skipped_no_document_id"] += 1
            continue
        ident = target_identity(row)
        if not ident:
            stats["skipped_missing_identity_fields"] += 1
            continue
        by_new[ident].append(row)

    survivors: Dict[str, Tuple[str, str]] = {}
    to_delete: List[str] = [d["document_id"] for d in superseded_symbols if d.get("document_id")]

    for (new_type, new_id), group in by_new.items():
        group.sort(key=_rank, reverse=True)
        winner, losers = group[0], group[1:]
        stats[f"{new_type}_keys"] += 1
        stats["duplicate_keys_dropped"] += len(losers)
        to_delete.extend(r["document_id"] for r in losers if r.get("document_id"))

        if winner["document_id"] == new_id and winner.get("type") == new_type:
            stats["already_stable"] += 1
        else:
            survivors[winner["document_id"]] = (new_type, new_id)
            if winner.get("type") != new_type:
                stats["reclassified_to_semantic_unit"] += 1

    return survivors, to_delete, id_map, stats


def to_semantic_unit(doc: Dict) -> Dict:
    """
    Rewrite a chunker-derived symbol_index doc into a semantic_unit.

    `symbol_name` becomes `label` — the field name is the point: it stops the
    LLM's description of a region from being read as an identifier.
    """
    meta = doc.get("metadata") or {}
    doc["type"] = "semantic_unit"
    doc["label"] = doc.pop("symbol_name", "") or ""
    doc["unit_type"] = doc.pop("symbol_type", "") or ""
    meta.pop("docstring", None)  # a chunker "purpose", never a real docstring
    meta.setdefault("purpose", "")
    doc["metadata"] = meta
    return doc


def derived_parent_id(doc: Dict) -> Optional[str]:
    """
    A document's parent computed from its own location.

    Preferred over the old→new map because it does not require the referent to
    still exist. Many `parent_id`s point at generations deleted by earlier
    cleanups, so they are absent from any map built over live documents — 9,830
    file→module links were dangling for exactly that reason.
    """
    repo_id = doc.get("repo_id")
    file_path = doc.get("file_path")
    if not repo_id:
        return None
    if doc.get("type") in ("symbol_index", "semantic_unit") and file_path:
        return make_file_id(repo_id, file_path)
    if doc.get("type") == "file_index" and file_path:
        # module_path is the containing folder, "" at the repo root.
        return make_module_id(repo_id, posixpath.dirname(file_path))
    return None


def remap_refs(doc: Dict, id_map: Dict[str, str]) -> Dict:
    """Point parent_id / children_ids at the new identities."""
    derived = derived_parent_id(doc)
    if derived:
        doc["parent_id"] = derived
    else:
        parent = doc.get("parent_id")
        if parent and parent in id_map:
            doc["parent_id"] = id_map[parent]

    children = doc.get("children_ids")
    if isinstance(children, list):
        doc["children_ids"] = [id_map.get(c, c) for c in children]
    return doc


def repair_refs(cb: CouchbaseClient, repo_id: str, execute: bool) -> Counter:
    """
    Re-point `parent_id` at the derived parent wherever it disagrees.

    Uses a subdocument mutation so the embedding is never read or rewritten —
    these documents carry a 768-float vector each and only one scalar changes.
    """
    stats = Counter()
    query = f"""
        SELECT d.document_id, d.type, d.repo_id, d.file_path, d.parent_id
        FROM `{BUCKET}` d
        WHERE d.repo_id = $repo_id
          AND d.type IN ['symbol_index', 'semantic_unit', 'file_index']
    """
    rows = list(cb.cluster.query(query, QueryOptions(named_parameters={"repo_id": repo_id})))

    for row in rows:
        want = derived_parent_id(row)
        if not want or row.get("parent_id") == want:
            continue
        stats["wrong_parent"] += 1
        if not execute:
            continue
        try:
            cb.collection.mutate_in(row["document_id"], [SD.upsert("parent_id", want)])
            stats["repaired"] += 1
        except Exception as e:
            logger.warning(f"  {repo_id}: could not repair {row['document_id'][:12]}: {e}")
            stats["repair_failed"] += 1
    return stats


def migrate_repo(cb: CouchbaseClient, repo_id: str, execute: bool) -> Counter:
    rows = fetch_rows(cb, repo_id)
    if not rows:
        return Counter()

    survivors, to_delete, id_map, stats = plan_repo(rows)

    if not execute:
        stats["would_move"] = len(survivors)
        stats["would_delete"] = len(to_delete)
        return stats

    moved = 0
    for old_id, (new_type, new_id) in survivors.items():
        try:
            doc = cb.collection.get(old_id).content_as[dict]
        except Exception as e:
            logger.warning(f"  {repo_id}: could not read {old_id[:12]}: {e}")
            stats["read_failed"] += 1
            continue

        if new_type == "semantic_unit" and doc.get("type") != "semantic_unit":
            doc = to_semantic_unit(doc)
        doc["document_id"] = new_id
        doc = remap_refs(doc, id_map)

        try:
            # Durable replacement before any removal: a crash here costs a
            # duplicate, which a re-run resolves, not a document.
            cb.collection.upsert(new_id, doc)
            moved += 1
        except Exception as e:
            logger.warning(f"  {repo_id}: could not write {new_id[:12]}: {e}")
            stats["write_failed"] += 1
            continue

        if old_id != new_id:
            try:
                cb.collection.remove(old_id)
            except Exception as e:
                logger.warning(f"  {repo_id}: could not remove {old_id[:12]}: {e}")
                stats["old_remove_failed"] += 1

    removed = 0
    for doc_id in to_delete:
        try:
            cb.collection.remove(doc_id)
            removed += 1
        except Exception:
            stats["superseded_already_absent"] += 1  # expected on a re-run

    stats["moved"] = moved
    stats["deleted"] = removed
    return stats


def list_repos(cb: CouchbaseClient) -> List[str]:
    query = f"""
        SELECT DISTINCT d.repo_id
        FROM `{BUCKET}` d
        WHERE d.type IN $types AND d.repo_id IS NOT MISSING
    """
    result = cb.cluster.query(query, QueryOptions(named_parameters={"types": list(TYPES)}))
    return sorted(r["repo_id"] for r in result if r.get("repo_id"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--execute", action="store_true", help="actually write; default is a dry run")
    ap.add_argument("--repo", help="restrict to one repo_id")
    ap.add_argument(
        "--repair-refs", action="store_true",
        help="only re-point parent_id at the derived parent; skips the re-keying pass",
    )
    args = ap.parse_args()

    cb = CouchbaseClient()
    repos = [args.repo] if args.repo else list_repos(cb)
    logger.info(f"{'MIGRATING' if args.execute else 'DRY RUN over'} {len(repos)} repositories")

    totals = Counter()
    for i, repo_id in enumerate(repos, 1):
        if args.repair_refs:
            stats = repair_refs(cb, repo_id, args.execute)
            if stats.get("wrong_parent"):
                logger.info(
                    f"[{i}/{len(repos)}] {repo_id}: "
                    f"{stats.get('repaired', stats['wrong_parent'])} parent links repaired"
                )
        else:
            stats = migrate_repo(cb, repo_id, args.execute)
            if stats:
                moved = stats.get("moved", stats.get("would_move", 0))
                dropped = stats.get("deleted", stats.get("would_delete", 0))
                logger.info(f"[{i}/{len(repos)}] {repo_id}: {moved} re-keyed, {dropped} removed")
        totals.update(stats)

    logger.info("=" * 60)
    for k in sorted(totals):
        logger.info(f"  {k:<34} {totals[k]:>8}")
    if not args.execute:
        logger.info("Dry run — nothing was written. Re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
