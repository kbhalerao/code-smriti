"""`smriti` — a portable CLI over the CodeSmriti RAG API.

A thin presenter over ``smriti_client.SmritiClient``: every subcommand makes one
API call and prints the result. Output is JSON on stdout by default (composes
with ``jq`` and scripts); ``--pretty`` renders the shared markdown formatters.

Errors go to stderr and map to distinct exit codes:
    0  success
    1  generic API/transport error
    2  authentication or configuration error
    3  resource not found
"""

import argparse
import asyncio
import json
import sys
from typing import Any, Callable

from dotenv import load_dotenv

from smriti_client import (
    SmritiAuthError,
    SmritiClient,
    SmritiConfigError,
    SmritiError,
    SmritiNotFoundError,
)
from smriti_client import format as fmt

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
EXIT_NOT_FOUND = 3

DEFAULT_CLUSTER = "kbhalerao/labcore"


def _add_global_opts(p: argparse.ArgumentParser, *, suppress: bool) -> None:
    """Attach the global flags to a parser.

    They are added both to the top-level parser (with real defaults) and to a
    shared parent of every subparser (with SUPPRESS defaults), so they work
    whether written before or after the subcommand — e.g. both
    ``smriti --pretty repos`` and ``smriti repos --pretty``. SUPPRESS on the
    subparser copy means an absent flag leaves the top-level value untouched
    rather than clobbering it with a default.
    """
    p.add_argument(
        "--pretty",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="Render human-readable markdown instead of JSON.",
    )
    p.add_argument(
        "--token",
        default=argparse.SUPPRESS if suppress else None,
        help="API token (overrides CODESMRITI_TOKEN / COS_TOKEN).",
    )
    p.add_argument(
        "--api-url",
        default=argparse.SUPPRESS if suppress else None,
        help="API base URL (overrides CODESMRITI_API_URL).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smriti",
        description="Search and explore CodeSmriti-indexed codebases.",
    )
    _add_global_opts(parser, suppress=False)

    # Shared parent so global flags are also accepted after the subcommand
    # (e.g. `smriti repos --pretty`, not just `smriti --pretty repos`).
    common = argparse.ArgumentParser(add_help=False)
    _add_global_opts(common, suppress=True)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("repos", parents=[common], help="List indexed repositories.")

    p = sub.add_parser(
        "structure", parents=[common], help="Explore a repo's directory structure."
    )
    p.add_argument("repo")
    p.add_argument("path", nargs="?", default="")
    p.add_argument("--pattern", help="Glob filter, e.g. '*.py'.")
    p.add_argument(
        "--summaries", action="store_true", help="Include module summaries."
    )

    p = sub.add_parser(
        "search", parents=[common], help="Semantic search across the codebase."
    )
    p.add_argument("query")
    p.add_argument(
        "--level",
        default="file",
        choices=["symbol", "file", "module", "repo", "doc", "spec"],
    )
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--repo", help="Filter to a single repo, e.g. 'owner/name'.")
    p.add_argument(
        "--preview", action="store_true", help="Return shortened content."
    )

    p = sub.add_parser(
        "ask", parents=[common], help="RAG answer about the codebase (developer)."
    )
    p.add_argument("query")

    p = sub.add_parser(
        "agsci",
        parents=[common],
        help="RAG answer about AgSci capabilities (sales).",
    )
    p.add_argument("query")

    p = sub.add_parser(
        "file", parents=[common], help="Fetch source code from an indexed file."
    )
    p.add_argument("repo")
    p.add_argument("path")
    p.add_argument("--start", type=int, help="Start line (1-indexed).")
    p.add_argument("--end", type=int, help="End line (1-indexed, inclusive).")

    graph = sub.add_parser(
        "graph", parents=[common], help="Dependency-graph queries."
    )
    graph_sub = graph.add_subparsers(dest="graph_command", required=True)

    g = graph_sub.add_parser(
        "info", parents=[common], help="Summary of a dependency graph."
    )
    g.add_argument("--cluster", default=DEFAULT_CLUSTER)

    g = graph_sub.add_parser(
        "criticality", parents=[common], help="Module importance (PageRank)."
    )
    g.add_argument("module")
    g.add_argument("--cluster", default=DEFAULT_CLUSTER)

    g = graph_sub.add_parser(
        "affected-tests", parents=[common], help="Tests impacted by changed files."
    )
    g.add_argument("files", nargs="+", help="Changed file paths.")
    g.add_argument("--cluster", default=DEFAULT_CLUSTER)

    return parser


async def dispatch(
    client: SmritiClient, args: argparse.Namespace
) -> tuple[dict[str, Any], Callable[[], str]]:
    """Run the selected command, returning (raw_data, pretty_renderer)."""
    cmd = args.command

    if cmd == "repos":
        data = await client.repos()
        return data, lambda: fmt.format_repos(data)

    if cmd == "structure":
        data = await client.structure(
            args.repo, args.path, args.pattern, args.summaries
        )
        return data, lambda: fmt.format_structure(args.repo, args.path, data)

    if cmd == "search":
        data = await client.search(
            args.query, args.level, args.limit, args.repo, args.preview
        )
        return data, lambda: fmt.format_search(args.level, args.preview, data)

    if cmd == "ask":
        data = await client.ask_code(args.query)
        return data, lambda: fmt.format_ask(data)

    if cmd == "agsci":
        data = await client.ask_proposal(args.query)
        return data, lambda: fmt.format_proposal(data)

    if cmd == "file":
        data = await client.get_file(args.repo, args.path, args.start, args.end)
        return data, lambda: fmt.format_file(args.repo, args.path, data)

    if cmd == "graph":
        gcmd = args.graph_command
        if gcmd == "info":
            data = await client.graph_info(args.cluster)
            return data, lambda: fmt.format_graph_info(args.cluster, data)
        if gcmd == "criticality":
            data = await client.criticality(args.module, args.cluster)
            return data, lambda: fmt.format_criticality(args.module, data)
        if gcmd == "affected-tests":
            data = await client.affected_tests(args.files, args.cluster)
            return data, lambda: fmt.format_affected_tests(args.cluster, data)

    raise SmritiError(f"Unknown command: {cmd}")


def emit_error(message: str, kind: str, pretty: bool) -> None:
    if pretty:
        print(f"Error: {message}", file=sys.stderr)
    else:
        json.dump({"error": message, "type": kind}, sys.stderr)
        sys.stderr.write("\n")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # best-effort; real env vars and flags take precedence
    args = build_parser().parse_args(argv)
    client = SmritiClient(token=args.token, base_url=args.api_url)

    try:
        data, render_pretty = asyncio.run(dispatch(client, args))
    except (SmritiAuthError, SmritiConfigError) as e:
        emit_error(str(e), "auth", args.pretty)
        return EXIT_AUTH
    except SmritiNotFoundError as e:
        emit_error(str(e), "not_found", args.pretty)
        return EXIT_NOT_FOUND
    except SmritiError as e:
        emit_error(str(e), "error", args.pretty)
        return EXIT_ERROR

    if args.pretty:
        print(render_pretty())
    else:
        print(json.dumps(data, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
