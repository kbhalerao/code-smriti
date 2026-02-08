"""
RAG Tool Models

Pydantic models for tool inputs and outputs.
"""

from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class SearchLevel(str, Enum):
    """Granularity level for search operations."""
    SYMBOL = "symbol"      # symbol_index - functions, classes
    FILE = "file"          # file_index - individual files
    MODULE = "module"      # module_summary - folders/directories
    REPO = "repo"          # repo_summary - repository overview
    DOC = "doc"            # document - documentation files (RST, MD, etc.)
    SPEC = "spec"          # spec - feature specs with L0-L5 constraints


# Maps SearchLevel to V4 document types
LEVEL_TO_DOCTYPE = {
    SearchLevel.SYMBOL: "symbol_index",
    SearchLevel.FILE: "file_index",
    SearchLevel.MODULE: "module_summary",
    SearchLevel.REPO: "repo_summary",
    SearchLevel.DOC: "document",
    SearchLevel.SPEC: "spec",
}


class RepoInfo(BaseModel):
    """Repository information."""
    repo_id: str = Field(description="Repository identifier (owner/repo)")
    doc_count: int = Field(description="Number of indexed documents")
    languages: List[str] = Field(default_factory=list, description="Primary languages")


class FileInfo(BaseModel):
    """File information within a directory."""
    name: str = Field(description="File name")
    path: str = Field(description="Full path relative to repo root")
    language: str = Field(default="", description="Programming language")
    line_count: int = Field(default=0, description="Number of lines")
    has_summary: bool = Field(default=False, description="True if file_index exists")


class StructureInfo(BaseModel):
    """Directory structure information."""
    repo_id: str = Field(description="Repository identifier")
    path: str = Field(description="Current path (empty string for root)")
    directories: List[str] = Field(default_factory=list, description="Subdirectories")
    files: List[FileInfo] = Field(default_factory=list, description="Files in directory")
    key_files: Dict[str, str] = Field(
        default_factory=dict,
        description="Auto-detected key files: {type: path}"
    )
    summary: Optional[str] = Field(
        default=None,
        description="Module summary content if include_summaries=True"
    )


class SearchResult(BaseModel):
    """Search result from semantic search."""
    document_id: str = Field(description="Couchbase document ID")
    doc_type: str = Field(description="Document type (symbol_index, file_index, etc.)")
    repo_id: str = Field(description="Repository identifier")
    file_path: Optional[str] = Field(default=None, description="File path (for file/symbol)")
    symbol_name: Optional[str] = Field(default=None, description="Symbol name (for symbol)")
    symbol_type: Optional[str] = Field(default=None, description="Symbol type (function, class)")
    content: str = Field(description="LLM-generated summary")
    score: float = Field(description="Relevance score")

    # Hierarchy navigation
    parent_id: Optional[str] = Field(default=None, description="Parent document ID")
    children_ids: List[str] = Field(default_factory=list, description="Child document IDs")

    # For code retrieval
    start_line: Optional[int] = Field(default=None, description="Start line number")
    end_line: Optional[int] = Field(default=None, description="End line number")


class FileContent(BaseModel):
    """File content from get_file."""
    repo_id: str = Field(description="Repository identifier")
    file_path: str = Field(description="File path")
    code: str = Field(description="Actual source code")
    start_line: int = Field(description="Start line returned")
    end_line: int = Field(description="End line returned")
    total_lines: int = Field(description="Total lines in file")
    language: str = Field(default="", description="Programming language")
    truncated: bool = Field(default=False, description="True if content was truncated")
