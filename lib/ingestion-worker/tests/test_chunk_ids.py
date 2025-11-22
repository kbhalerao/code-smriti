"""
Unit tests for chunk ID generation and deduplication logic
"""

import unittest
import hashlib
from pathlib import Path
import tempfile
import shutil
import git

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.code_parser import CodeChunk, CodeParser
from parsers.document_parser import DocumentChunk, DocumentParser


class TestChunkIDGeneration(unittest.TestCase):
    """Test deterministic chunk ID generation"""

    def test_code_chunk_id_uniqueness_different_files(self):
        """Different files should have different chunk IDs"""
        chunk1 = CodeChunk(
            repo_id="test/repo",
            file_path="file1.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"commit_hash": "abc123", "start_line": 1}
        )

        chunk2 = CodeChunk(
            repo_id="test/repo",
            file_path="file2.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"commit_hash": "abc123", "start_line": 1}
        )

        self.assertNotEqual(chunk1.chunk_id, chunk2.chunk_id,
                          "Different files should have different chunk IDs")

    def test_code_chunk_id_uniqueness_different_lines(self):
        """Different start lines should have different chunk IDs"""
        chunk1 = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"commit_hash": "abc123", "start_line": 1}
        )

        chunk2 = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def bar(): pass",
            language="python",
            metadata={"commit_hash": "abc123", "start_line": 10}
        )

        self.assertNotEqual(chunk1.chunk_id, chunk2.chunk_id,
                          "Different start lines should have different chunk IDs")

    def test_code_chunk_id_uniqueness_different_commits(self):
        """Different commits should have different chunk IDs"""
        chunk1 = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"commit_hash": "abc123", "start_line": 1}
        )

        chunk2 = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): return 42",  # Changed code
            language="python",
            metadata={"commit_hash": "def456", "start_line": 1}
        )

        self.assertNotEqual(chunk1.chunk_id, chunk2.chunk_id,
                          "Different commits should have different chunk IDs")

    def test_code_chunk_id_deterministic(self):
        """Same inputs should produce same chunk ID"""
        metadata = {"commit_hash": "abc123", "start_line": 5}

        chunk1 = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata=metadata
        )

        chunk2 = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata=metadata
        )

        self.assertEqual(chunk1.chunk_id, chunk2.chunk_id,
                        "Identical inputs should produce identical chunk IDs")

    def test_code_chunk_id_with_missing_commit(self):
        """Missing commit hash should use 'no_commit' default"""
        chunk = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"start_line": 1}  # No commit_hash
        )

        # Manually compute expected ID
        expected_key = "test/repo:file.py:no_commit:1"
        expected_id = hashlib.sha256(expected_key.encode()).hexdigest()

        self.assertEqual(chunk.chunk_id, expected_id,
                        "Missing commit should default to 'no_commit'")

    def test_code_chunk_id_with_missing_start_line(self):
        """Missing start_line should use 0 as default"""
        chunk = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"commit_hash": "abc123"}  # No start_line
        )

        # Manually compute expected ID
        expected_key = "test/repo:file.py:abc123:0"
        expected_id = hashlib.sha256(expected_key.encode()).hexdigest()

        self.assertEqual(chunk.chunk_id, expected_id,
                        "Missing start_line should default to 0")

    def test_document_chunk_id_uniqueness(self):
        """Different document files should have different chunk IDs"""
        chunk1 = DocumentChunk(
            repo_id="test/repo",
            file_path="README.md",
            doc_type="markdown",
            content="# Hello World",
            metadata={"commit_hash": "abc123"}
        )

        chunk2 = DocumentChunk(
            repo_id="test/repo",
            file_path="INSTALL.md",
            doc_type="markdown",
            content="# Hello World",
            metadata={"commit_hash": "abc123"}
        )

        self.assertNotEqual(chunk1.chunk_id, chunk2.chunk_id,
                          "Different document files should have different chunk IDs")

    def test_document_chunk_id_no_start_line(self):
        """Document chunks should not include start_line in ID"""
        chunk = DocumentChunk(
            repo_id="test/repo",
            file_path="README.md",
            doc_type="markdown",
            content="# Hello World",
            metadata={"commit_hash": "abc123"}
        )

        # Manually compute expected ID (no start_line)
        expected_key = "test/repo:README.md:abc123"
        expected_id = hashlib.sha256(expected_key.encode()).hexdigest()

        self.assertEqual(chunk.chunk_id, expected_id,
                        "Document chunk ID should not include start_line")


class TestGitMetadataExtraction(unittest.TestCase):
    """Test git metadata extraction"""

    def setUp(self):
        """Create a temporary git repository for testing"""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)

        # Initialize git repo
        self.repo = git.Repo.init(self.repo_path)

        # Configure git
        with self.repo.config_writer() as config:
            config.set_value("user", "name", "Test User")
            config.set_value("user", "email", "test@example.com")

        # Create a test file and commit
        test_file = self.repo_path / "test.py"
        test_file.write_text("def hello(): pass\n")
        self.repo.index.add(["test.py"])
        self.commit1 = self.repo.index.commit("Initial commit")

        # Modify and commit again
        test_file.write_text("def hello(): return 'world'\n")
        self.repo.index.add(["test.py"])
        self.commit2 = self.repo.index.commit("Update hello function")

        self.parser = CodeParser()

    def tearDown(self):
        """Clean up temporary directory"""
        shutil.rmtree(self.temp_dir)

    def test_git_metadata_extraction_success(self):
        """Should extract git metadata for tracked file"""
        metadata = self.parser.get_git_metadata(self.repo_path, "test.py")

        self.assertIn("commit_hash", metadata)
        self.assertIn("commit_date", metadata)
        self.assertIn("author", metadata)
        self.assertIn("commit_message", metadata)

        # Should get the latest commit
        self.assertEqual(metadata["commit_hash"], self.commit2.hexsha)
        self.assertEqual(metadata["author"], "test@example.com")
        self.assertIn("Update hello", metadata["commit_message"])

    def test_git_metadata_extraction_nonexistent_file(self):
        """Should return empty dict for non-existent file"""
        metadata = self.parser.get_git_metadata(self.repo_path, "nonexistent.py")

        self.assertEqual(metadata, {},
                        "Non-existent file should return empty metadata")

    def test_git_metadata_extraction_untracked_file(self):
        """Should return empty dict for untracked file"""
        untracked_file = self.repo_path / "untracked.py"
        untracked_file.write_text("def untracked(): pass\n")

        metadata = self.parser.get_git_metadata(self.repo_path, "untracked.py")

        self.assertEqual(metadata, {},
                        "Untracked file should return empty metadata")


class TestChunkDeduplication(unittest.TestCase):
    """Test that chunks don't collide unexpectedly"""

    def test_many_chunks_same_file_unique_ids(self):
        """Multiple chunks from same file should have unique IDs"""
        repo_id = "test/repo"
        file_path = "large_file.py"
        commit_hash = "abc123"

        chunks = []
        for i in range(100):
            chunk = CodeChunk(
                repo_id=repo_id,
                file_path=file_path,
                chunk_type="function",
                code_text=f"def func{i}(): pass",
                language="python",
                metadata={"commit_hash": commit_hash, "start_line": i * 10}
            )
            chunks.append(chunk)

        # All chunk IDs should be unique
        chunk_ids = [c.chunk_id for c in chunks]
        unique_ids = set(chunk_ids)

        self.assertEqual(len(chunk_ids), len(unique_ids),
                        f"Expected 100 unique IDs, got {len(unique_ids)}. "
                        f"Collisions: {len(chunk_ids) - len(unique_ids)}")

    def test_no_collision_with_no_commit_hash(self):
        """Chunks without commit hash should still be unique if other params differ"""
        chunks = []
        for i in range(10):
            chunk = CodeChunk(
                repo_id="test/repo",
                file_path=f"file{i}.py",
                chunk_type="function",
                code_text="def foo(): pass",
                language="python",
                metadata={"start_line": 1}  # No commit_hash
            )
            chunks.append(chunk)

        chunk_ids = [c.chunk_id for c in chunks]
        unique_ids = set(chunk_ids)

        self.assertEqual(len(chunk_ids), len(unique_ids),
                        f"Expected 10 unique IDs (no commit), got {len(unique_ids)}")

    def test_collision_scenario(self):
        """Test the exact scenario causing 80% deduplication"""
        # Simulate: same repo_id, no commit hash, different files
        repo_id = "test/code-smriti"
        commit_hash = "no_commit"

        chunks = []
        for i in range(100):
            chunk = CodeChunk(
                repo_id=repo_id,
                file_path=f"repos/kbhalerao_labcore/file{i}.js",
                chunk_type="function",
                code_text=f"function func{i}() {{}}",
                language="javascript",
                metadata={"commit_hash": commit_hash, "start_line": i + 1}
            )
            chunks.append(chunk)

        chunk_ids = [c.chunk_id for c in chunks]
        unique_ids = set(chunk_ids)

        collision_count = len(chunk_ids) - len(unique_ids)

        self.assertEqual(collision_count, 0,
                        f"Found {collision_count} collisions! This might explain the deduplication bug.")


class TestChunkSerialization(unittest.TestCase):
    """Test chunk serialization to dict"""

    def test_code_chunk_to_dict(self):
        """CodeChunk.to_dict() should include all fields"""
        chunk = CodeChunk(
            repo_id="test/repo",
            file_path="file.py",
            chunk_type="function",
            code_text="def foo(): pass",
            language="python",
            metadata={"commit_hash": "abc123", "start_line": 5}
        )

        data = chunk.to_dict()

        self.assertIn("chunk_id", data)
        self.assertIn("type", data)
        self.assertIn("repo_id", data)
        self.assertIn("file_path", data)
        self.assertIn("chunk_type", data)
        self.assertIn("code_text", data)
        self.assertIn("language", data)
        self.assertIn("metadata", data)
        self.assertIn("embedding", data)
        self.assertIn("created_at", data)

        self.assertEqual(data["repo_id"], "test/repo")
        self.assertEqual(data["file_path"], "file.py")

    def test_document_chunk_to_dict(self):
        """DocumentChunk.to_dict() should include all fields"""
        chunk = DocumentChunk(
            repo_id="test/repo",
            file_path="README.md",
            doc_type="markdown",
            content="# Hello",
            metadata={"commit_hash": "abc123"}
        )

        data = chunk.to_dict()

        self.assertIn("chunk_id", data)
        self.assertIn("type", data)
        self.assertIn("repo_id", data)
        self.assertIn("file_path", data)
        self.assertIn("doc_type", data)
        self.assertIn("content", data)
        self.assertIn("metadata", data)
        self.assertIn("embedding", data)
        self.assertIn("created_at", data)


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)
