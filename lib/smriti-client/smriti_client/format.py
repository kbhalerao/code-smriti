"""Markdown formatters for CodeSmriti API responses.

Shared by the MCP server (LLM-facing output) and the CLI's ``--pretty`` mode
(human-facing output) so there is exactly one rendering of each response shape.
Every function takes the parsed API dict and returns a markdown string.
"""

from typing import Any


def format_repos(data: dict[str, Any]) -> str:
    repos = data.get("repos", [])
    if not repos:
        return "No repositories indexed."

    output = ["## Indexed Repositories\n"]
    for repo in repos:
        name = repo.get("repo_id", "Unknown")
        doc_count = repo.get("doc_count", 0)
        languages = repo.get("languages", [])
        lang_str = f" ({', '.join(languages[:3])})" if languages else ""
        output.append(f"- **{name}**: {doc_count} documents{lang_str}")

    output.append(
        f"\n_Total: {data.get('total_repos', 0)} repos, "
        f"{data.get('total_docs', 0)} documents_"
    )
    return "\n".join(output)


def format_structure(repo_id: str, path: str, data: dict[str, Any]) -> str:
    output = [f"## {repo_id}/{path or '(root)'}\n"]

    key_files = data.get("key_files", {})
    if key_files:
        output.append("**Key files:**")
        for key_type, key_path in key_files.items():
            output.append(f"  - {key_type}: `{key_path}`")
        output.append("")

    directories = data.get("directories", [])
    if directories:
        output.append("**Directories:**")
        for d in directories:
            output.append(f"  - {d}")
        output.append("")

    files = data.get("files", [])
    if files:
        output.append("**Files:**")
        for f in files:
            name = f.get("name", "")
            lang = f.get("language", "")
            lines = f.get("line_count", 0)
            has_summary = "indexed" if f.get("has_summary") else ""
            lang_str = f" ({lang})" if lang else ""
            output.append(f"  - `{name}`{lang_str} - {lines} lines {has_summary}")
        output.append("")

    summary = data.get("summary")
    if summary:
        output.append("**Module Summary:**")
        output.append(summary)

    if not directories and not files:
        output.append("_Empty or not found_")

    return "\n".join(output)


def format_search(level: str, preview: bool, data: dict[str, Any]) -> str:
    results = data.get("results", [])
    if not results:
        return f"No results found at {level} level."

    mode_note = " (preview)" if preview else ""
    output = [f"## Search Results ({level} level{mode_note})\n"]

    for r in results:
        doc_type = r.get("doc_type", "")
        repo_id = r.get("repo_id", "")
        file_path = r.get("file_path", "")
        symbol_name = r.get("symbol_name", "")
        content = r.get("content", "")
        score = r.get("score", 0)
        start_line = r.get("start_line")
        end_line = r.get("end_line")

        if doc_type == "symbol_index":
            symbol_type = r.get("symbol_type", "symbol")
            header = f"### {symbol_name} ({symbol_type}) in {file_path}"
            if start_line and end_line:
                header += f" [lines {start_line}-{end_line}]"
        elif doc_type == "file_index":
            header = f"### {file_path}"
        elif doc_type == "module_summary":
            module_path = r.get("module_path", file_path or "")
            header = f"### Module: {module_path}/"
        elif doc_type == "repo_summary":
            header = f"### Repository: {repo_id}"
        elif doc_type == "spec":
            spec_name = r.get("symbol_name", "")
            label = f"Spec: {spec_name}" if spec_name else f"Spec: {file_path}"
            header = f"### {label}"
        elif doc_type == "document":
            doc_subtype = r.get("symbol_type", "doc")
            header = f"### Doc: {file_path} ({doc_subtype})"
        else:
            header = f"### {file_path or repo_id}"

        output.append(header)
        output.append(f"_Repo: {repo_id} | Score: {score:.2f}_\n")

        max_len = 200 if preview else 500
        output.append(content[:max_len] + ("..." if len(content) > max_len else ""))
        output.append("")

    return "\n".join(output)


def format_ask(data: dict[str, Any]) -> str:
    answer = data.get("answer", "No answer received.")
    sources = data.get("sources", [])
    gaps = data.get("gaps", [])

    result = answer
    if sources:
        result += "\n\n**Sources:**\n" + "\n".join(f"- {s}" for s in sources[:5])
    if gaps:
        result += "\n\n**Gaps identified:**\n" + "\n".join(f"- {g}" for g in gaps)
    return result


def format_proposal(data: dict[str, Any]) -> str:
    answer = data.get("answer", "No answer received.")
    sources = data.get("sources", [])
    gaps = data.get("gaps", [])
    intent = data.get("intent", "")

    result = answer
    if gaps:
        result += "\n\n**Gaps (need more input):**\n" + "\n".join(
            f"- {g}" for g in gaps
        )
    if sources:
        result += "\n\n**Sources:**\n" + "\n".join(f"- {s}" for s in sources[:5])
    if intent:
        result += f"\n\n_Intent: {intent}_"
    return result


def format_file(repo_id: str, file_path: str, data: dict[str, Any]) -> str:
    code = data.get("code", "")
    start = data.get("start_line", 1)
    end = data.get("end_line", 0)
    total = data.get("total_lines", 0)
    language = data.get("language", "")
    truncated = data.get("truncated", False)

    header = f"## {repo_id}/{file_path}\n"
    header += f"Lines {start}-{end} of {total}"
    if truncated:
        header += " (truncated)"
    header += "\n\n"

    return header + f"```{language}\n{code}\n```"


def format_affected_tests(cluster_id: str, data: dict[str, Any]) -> str:
    if not data.get("graph_found"):
        return f"No dependency graph found for cluster '{cluster_id}'. Run all tests."

    changed = data.get("changed_modules", [])
    affected = data.get("affected_modules", [])
    tests = data.get("tests_to_run", [])

    output = [f"## Affected Tests for {cluster_id}\n"]

    if changed:
        output.append(f"**Changed modules:** {len(changed)}")
        for m in changed[:10]:
            output.append(f"  - {m}")
        if len(changed) > 10:
            output.append(f"  - ... and {len(changed) - 10} more")
        output.append("")

    if affected:
        output.append(f"**Affected modules:** {len(affected)}")
        for m in affected[:10]:
            output.append(f"  - {m}")
        if len(affected) > 10:
            output.append(f"  - ... and {len(affected) - 10} more")
        output.append("")

    if tests:
        output.append(f"**Tests to run:** {len(tests)}")
        for t in tests:
            output.append(f"  - {t}")
    else:
        output.append("**No test modules affected**")

    return "\n".join(output)


def format_criticality(module: str, data: dict[str, Any]) -> str:
    score = data.get("score", 0)
    percentile = data.get("percentile", 0)
    in_deg = data.get("in_degree", 0)
    out_deg = data.get("out_degree", 0)
    repo_id = data.get("repo_id", "")
    is_test = data.get("is_test", False)
    dependents = data.get("direct_dependents", [])

    output = [f"## Criticality: {module}\n"]
    output.append(f"**Repo:** {repo_id}")
    output.append(f"**Score:** {score:.6f} (percentile: {percentile})")
    output.append(f"**In-degree:** {in_deg} (modules depend on this)")
    output.append(f"**Out-degree:** {out_deg} (modules this depends on)")
    if is_test:
        output.append("**Type:** Test module")
    output.append("")

    if dependents:
        output.append(f"**Direct dependents ({len(dependents)}):**")
        for d in dependents[:15]:
            output.append(f"  - {d}")
        if len(dependents) > 15:
            output.append(f"  - ... and {len(dependents) - 15} more")
    else:
        output.append("_No direct dependents (leaf module)_")

    return "\n".join(output)


def format_graph_info(cluster_id: str, data: dict[str, Any]) -> str:
    output = [f"## Dependency Graph: {cluster_id}\n"]
    output.append(f"**Nodes:** {data.get('total_nodes', 0)}")
    output.append(f"**Edges:** {data.get('total_edges', 0)}")
    output.append(f"**Cross-repo edges:** {data.get('cross_repo_edges', 0)}")
    output.append(f"**Computed at:** {data.get('computed_at', 'unknown')}")
    output.append("")

    repos = data.get("repos", {})
    if repos:
        output.append("**Repos in cluster:**")
        for repo_id, info in repos.items():
            role = info.get("role", "unknown")
            count = info.get("module_count", 0)
            output.append(f"  - {repo_id}: {count} modules ({role})")

    return "\n".join(output)
