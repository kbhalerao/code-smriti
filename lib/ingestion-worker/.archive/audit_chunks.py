#!/usr/bin/env python3
"""
Chunk Quality Audit Tool

Analyzes existing chunks in Couchbase to identify quality issues
and opportunities for improvement.

Run: python audit_chunks.py [--repo REPO_ID] [--sample N] [--export FILE]
"""

import asyncio
import json
import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import timedelta

from loguru import logger
from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions

from config import WorkerConfig

config = WorkerConfig()


@dataclass
class QualityIssue:
    """Represents a quality issue with a chunk"""
    chunk_id: str
    issue_type: str
    severity: str  # "high", "medium", "low"
    details: str
    file_path: str = ""
    repo_id: str = ""


@dataclass
class ChunkStats:
    """Statistics for a single chunk"""
    chunk_id: str
    type: str
    repo_id: str
    file_path: str
    content_length: int
    has_embedding: bool
    embedding_dim: int
    metadata_keys: List[str]
    quality_score: float = 0.0
    issues: List[QualityIssue] = field(default_factory=list)


@dataclass
class RepoAuditReport:
    """Audit report for a single repository"""
    repo_id: str
    total_chunks: int
    chunks_by_type: Dict[str, int]
    avg_content_length: float
    empty_chunks: int
    missing_embeddings: int
    quality_distribution: Dict[str, int]  # "high", "medium", "low", "critical"
    issues: List[QualityIssue]
    sample_issues: List[Dict]  # Sample of problematic chunks


class ChunkAuditor:
    """Audits chunk quality in Couchbase"""

    def __init__(self):
        logger.info(f"Connecting to Couchbase at {config.couchbase_host}")

        connection_string = f"couchbase://{config.couchbase_host}"
        auth = PasswordAuthenticator(config.couchbase_username, config.couchbase_password)

        self.cluster = Cluster(connection_string, ClusterOptions(auth))
        self.cluster.wait_until_ready(timedelta(seconds=10))
        self.bucket = self.cluster.bucket(config.couchbase_bucket)
        self.collection = self.bucket.default_collection()

        logger.info(f"Connected to bucket: {config.couchbase_bucket}")

    def get_all_repos(self) -> List[str]:
        """Get list of all repository IDs in the database"""
        query = f"""
            SELECT DISTINCT repo_id
            FROM `{config.couchbase_bucket}`
            WHERE repo_id IS NOT NULL
        """
        result = self.cluster.query(query)
        return [row["repo_id"] for row in result]

    def get_repo_chunks(self, repo_id: str, limit: Optional[int] = None) -> List[Dict]:
        """Get all chunks for a repository"""
        query = f"""
            SELECT META().id as chunk_id, *
            FROM `{config.couchbase_bucket}`
            WHERE repo_id = $repo_id
        """
        if limit:
            query += f" LIMIT {limit}"

        result = self.cluster.query(query, repo_id=repo_id)
        return [row for row in result]

    def get_chunk_count_by_repo(self) -> Dict[str, int]:
        """Get chunk counts per repository"""
        query = f"""
            SELECT repo_id, COUNT(*) as count
            FROM `{config.couchbase_bucket}`
            WHERE repo_id IS NOT NULL
            GROUP BY repo_id
            ORDER BY count DESC
        """
        result = self.cluster.query(query)
        return {row["repo_id"]: row["count"] for row in result}

    def get_chunk_type_distribution(self, repo_id: Optional[str] = None) -> Dict[str, int]:
        """Get distribution of chunk types"""
        if repo_id:
            query = f"""
                SELECT type, COUNT(*) as count
                FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id
                GROUP BY type
            """
            result = self.cluster.query(query, repo_id=repo_id)
        else:
            query = f"""
                SELECT type, COUNT(*) as count
                FROM `{config.couchbase_bucket}`
                GROUP BY type
            """
            result = self.cluster.query(query)

        return {row["type"]: row["count"] for row in result}

    def calculate_chunk_quality(self, chunk: Dict) -> tuple[float, List[QualityIssue]]:
        """
        Calculate quality score for a chunk and identify issues

        Returns:
            (quality_score, list_of_issues)
        """
        issues = []
        score = 0.5  # Base score

        chunk_data = chunk.get(config.couchbase_bucket, chunk)
        chunk_id = chunk.get("chunk_id", "unknown")
        repo_id = chunk_data.get("repo_id", "unknown")
        file_path = chunk_data.get("file_path", "unknown")
        chunk_type = chunk_data.get("type", "unknown")
        content = chunk_data.get("content", "")
        embedding = chunk_data.get("embedding")
        metadata = chunk_data.get("metadata", {})

        # === Content Quality ===

        # Check for empty content
        if not content or len(content.strip()) < 10:
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="empty_content",
                severity="high",
                details=f"Content length: {len(content) if content else 0}",
                file_path=file_path,
                repo_id=repo_id
            ))
            score -= 0.3
        elif len(content.strip()) < 50:
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="minimal_content",
                severity="medium",
                details=f"Content length: {len(content)}",
                file_path=file_path,
                repo_id=repo_id
            ))
            score -= 0.1
        elif len(content) > 200:
            score += 0.1

        # Check for truncation markers
        if content and "truncated" in content.lower():
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="truncated_content",
                severity="medium",
                details="Content was truncated during ingestion",
                file_path=file_path,
                repo_id=repo_id
            ))
            score -= 0.1

        # Check for single-line class definitions (major issue!)
        if chunk_type in ["class", "symbol"] and content:
            lines = content.strip().split("\n")
            if len(lines) <= 2:
                issues.append(QualityIssue(
                    chunk_id=chunk_id,
                    issue_type="fragment_only",
                    severity="high",
                    details=f"Class/symbol has only {len(lines)} lines - missing implementation",
                    file_path=file_path,
                    repo_id=repo_id
                ))
                score -= 0.25

        # === Embedding Quality ===

        if not embedding:
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="missing_embedding",
                severity="high",
                details="No embedding vector",
                file_path=file_path,
                repo_id=repo_id
            ))
            score -= 0.2
        elif isinstance(embedding, list) and len(embedding) < 100:
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="invalid_embedding",
                severity="high",
                details=f"Embedding dimension too small: {len(embedding)}",
                file_path=file_path,
                repo_id=repo_id
            ))
            score -= 0.2
        else:
            score += 0.15

        # === Metadata Quality ===

        # Check for git metadata
        if not metadata.get("commit_hash"):
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="missing_git_metadata",
                severity="low",
                details="No commit hash in metadata",
                file_path=file_path,
                repo_id=repo_id
            ))
            score -= 0.05
        else:
            score += 0.05

        # Check for language detection
        if chunk_type == "code_chunk" and not metadata.get("language"):
            issues.append(QualityIssue(
                chunk_id=chunk_id,
                issue_type="missing_language",
                severity="low",
                details="Language not detected",
                file_path=file_path,
                repo_id=repo_id
            ))

        # Check for docstrings on code chunks
        if chunk_type in ["function", "method", "class"]:
            if metadata.get("docstring"):
                score += 0.1

        # === Structural Quality ===

        # Check if this is a file_metadata chunk with actual content
        if chunk_type == "file_metadata":
            if not content or len(content.strip()) < 100:
                issues.append(QualityIssue(
                    chunk_id=chunk_id,
                    issue_type="empty_metadata_chunk",
                    severity="medium",
                    details="File metadata chunk has no preview content",
                    file_path=file_path,
                    repo_id=repo_id
                ))
                score -= 0.15

        return (max(0.0, min(1.0, score)), issues)

    def audit_repo(self, repo_id: str, sample_size: Optional[int] = None) -> RepoAuditReport:
        """Audit a single repository"""
        logger.info(f"Auditing repository: {repo_id}")

        chunks = self.get_repo_chunks(repo_id, limit=sample_size)

        if not chunks:
            return RepoAuditReport(
                repo_id=repo_id,
                total_chunks=0,
                chunks_by_type={},
                avg_content_length=0,
                empty_chunks=0,
                missing_embeddings=0,
                quality_distribution={},
                issues=[],
                sample_issues=[]
            )

        all_issues = []
        chunks_by_type = defaultdict(int)
        content_lengths = []
        quality_scores = []
        empty_count = 0
        missing_embedding_count = 0

        for chunk in chunks:
            chunk_data = chunk.get(config.couchbase_bucket, chunk)
            chunk_type = chunk_data.get("type", "unknown")
            content = chunk_data.get("content", "")
            embedding = chunk_data.get("embedding")

            chunks_by_type[chunk_type] += 1
            content_lengths.append(len(content) if content else 0)

            if not content or len(content.strip()) < 10:
                empty_count += 1

            if not embedding:
                missing_embedding_count += 1

            quality_score, issues = self.calculate_chunk_quality(chunk)
            quality_scores.append(quality_score)
            all_issues.extend(issues)

        # Calculate quality distribution
        quality_dist = {"high": 0, "medium": 0, "low": 0, "critical": 0}
        for score in quality_scores:
            if score >= 0.8:
                quality_dist["high"] += 1
            elif score >= 0.6:
                quality_dist["medium"] += 1
            elif score >= 0.4:
                quality_dist["low"] += 1
            else:
                quality_dist["critical"] += 1

        # Get sample of worst issues
        high_severity = [i for i in all_issues if i.severity == "high"]
        sample_issues = []
        for issue in high_severity[:5]:
            sample_issues.append({
                "chunk_id": issue.chunk_id[:16] + "...",
                "file_path": issue.file_path,
                "issue": issue.issue_type,
                "details": issue.details
            })

        return RepoAuditReport(
            repo_id=repo_id,
            total_chunks=len(chunks),
            chunks_by_type=dict(chunks_by_type),
            avg_content_length=sum(content_lengths) / len(content_lengths) if content_lengths else 0,
            empty_chunks=empty_count,
            missing_embeddings=missing_embedding_count,
            quality_distribution=quality_dist,
            issues=all_issues,
            sample_issues=sample_issues
        )

    def audit_all(self, sample_per_repo: int = 100) -> Dict[str, Any]:
        """Audit all repositories"""
        repos = self.get_all_repos()
        logger.info(f"Found {len(repos)} repositories to audit")

        global_stats = {
            "total_repos": len(repos),
            "total_chunks": 0,
            "global_type_distribution": defaultdict(int),
            "global_quality_distribution": {"high": 0, "medium": 0, "low": 0, "critical": 0},
            "repos_with_issues": 0,
            "top_issues": defaultdict(int),
            "worst_repos": [],
            "repo_reports": {}
        }

        for repo_id in repos:
            report = self.audit_repo(repo_id, sample_size=sample_per_repo)

            global_stats["total_chunks"] += report.total_chunks

            for chunk_type, count in report.chunks_by_type.items():
                global_stats["global_type_distribution"][chunk_type] += count

            for quality_level, count in report.quality_distribution.items():
                global_stats["global_quality_distribution"][quality_level] += count

            if report.issues:
                global_stats["repos_with_issues"] += 1
                for issue in report.issues:
                    global_stats["top_issues"][issue.issue_type] += 1

            # Track worst repos
            if report.quality_distribution.get("critical", 0) > 0:
                global_stats["worst_repos"].append({
                    "repo_id": repo_id,
                    "critical_chunks": report.quality_distribution["critical"],
                    "total_chunks": report.total_chunks,
                    "sample_issues": report.sample_issues
                })

            global_stats["repo_reports"][repo_id] = {
                "total_chunks": report.total_chunks,
                "quality_distribution": report.quality_distribution,
                "empty_chunks": report.empty_chunks,
                "missing_embeddings": report.missing_embeddings,
                "issue_count": len(report.issues)
            }

        # Sort worst repos
        global_stats["worst_repos"] = sorted(
            global_stats["worst_repos"],
            key=lambda x: x["critical_chunks"],
            reverse=True
        )[:10]

        # Convert defaultdicts to regular dicts for JSON serialization
        global_stats["global_type_distribution"] = dict(global_stats["global_type_distribution"])
        global_stats["top_issues"] = dict(global_stats["top_issues"])

        return global_stats


def print_report(report: Dict[str, Any]):
    """Pretty print the audit report"""
    print("\n" + "=" * 80)
    print("CODESMRITI CHUNK QUALITY AUDIT REPORT")
    print("=" * 80)

    print(f"\n📊 OVERVIEW")
    print(f"   Total repositories: {report['total_repos']}")
    print(f"   Total chunks: {report['total_chunks']:,}")
    print(f"   Repos with issues: {report['repos_with_issues']}")

    print(f"\n📦 CHUNK TYPE DISTRIBUTION")
    for chunk_type, count in sorted(report["global_type_distribution"].items(), key=lambda x: -x[1]):
        pct = (count / report["total_chunks"]) * 100 if report["total_chunks"] > 0 else 0
        print(f"   {chunk_type}: {count:,} ({pct:.1f}%)")

    print(f"\n⭐ QUALITY DISTRIBUTION")
    for level in ["high", "medium", "low", "critical"]:
        count = report["global_quality_distribution"].get(level, 0)
        pct = (count / report["total_chunks"]) * 100 if report["total_chunks"] > 0 else 0
        emoji = {"high": "🟢", "medium": "🟡", "low": "🟠", "critical": "🔴"}[level]
        print(f"   {emoji} {level.capitalize()}: {count:,} ({pct:.1f}%)")

    print(f"\n🔴 TOP ISSUES")
    for issue_type, count in sorted(report["top_issues"].items(), key=lambda x: -x[1])[:10]:
        print(f"   {issue_type}: {count:,}")

    if report["worst_repos"]:
        print(f"\n⚠️ REPOS NEEDING ATTENTION")
        for repo in report["worst_repos"][:5]:
            print(f"   {repo['repo_id']}: {repo['critical_chunks']} critical chunks")
            for issue in repo.get("sample_issues", [])[:2]:
                print(f"      └─ {issue['file_path']}: {issue['issue']}")

    print("\n" + "=" * 80)


async def main():
    parser = argparse.ArgumentParser(description="Audit chunk quality in CodeSmriti")
    parser.add_argument("--repo", type=str, help="Audit specific repository")
    parser.add_argument("--sample", type=int, default=100, help="Sample size per repo")
    parser.add_argument("--export", type=str, help="Export report to JSON file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logger.add("audit.log", rotation="10 MB")

    auditor = ChunkAuditor()

    if args.repo:
        report = auditor.audit_repo(args.repo, sample_size=args.sample)
        result = {
            "repo_id": report.repo_id,
            "total_chunks": report.total_chunks,
            "chunks_by_type": report.chunks_by_type,
            "quality_distribution": report.quality_distribution,
            "issues_count": len(report.issues),
            "sample_issues": report.sample_issues
        }

        print(json.dumps(result, indent=2))
    else:
        report = auditor.audit_all(sample_per_repo=args.sample)
        print_report(report)

        if args.export:
            with open(args.export, "w") as f:
                json.dump(report, f, indent=2)
            print(f"\n📄 Report exported to: {args.export}")


if __name__ == "__main__":
    asyncio.run(main())
