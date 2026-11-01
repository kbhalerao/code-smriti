"""schema_enforced: does this ollama runner honour a json_schema?

Before 0.33.1 only the GGUF engine did; the MLX runner accepted the schema and
silently ignored it. The chunker's one-shot warning used to be `format != gguf`,
which turned into noise the day MLX gained enforcement.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_chunker import schema_enforced


@pytest.mark.parametrize("version", ["0.32.14", "0.35.1", None, "garbage"])
def test_gguf_is_enforced_on_every_version(version):
    assert schema_enforced("gguf", version) is True


@pytest.mark.parametrize("version", ["0.32.14", "0.32.99", "0.33.0"])
def test_mlx_before_0_33_1_ignores_the_schema(version):
    assert schema_enforced("safetensors", version) is False


@pytest.mark.parametrize("version", ["0.33.1", "0.33.3", "0.35.1", "0.40.0-rc3", "1.0.0"])
def test_mlx_from_0_33_1_enforces_it(version):
    assert schema_enforced("safetensors", version) is True


@pytest.mark.parametrize("version", [None, "", "garbage"])
def test_unknown_version_is_unknown_not_a_failure(version):
    # Not an ollama server, or /api/version unreachable: the caller must not warn.
    assert schema_enforced("safetensors", version) is None
