from __future__ import annotations

import hashlib
import math
import re


class HashEmbedding:
    """Deterministic local embedder used to evaluate Mem0 without a cloud model."""

    def __init__(self, config=None) -> None:
        self.config = config
        self.dimensions = int(getattr(config, "embedding_dims", None) or 64)

    def embed(self, text, memory_action=None):
        vector = [0.0] * self.dimensions
        normalized = str(text).lower()
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized)
        tokens.extend(normalized[index : index + 2] for index in range(max(0, len(normalized) - 1)))
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class DeterministicLLM:
    """No-network Mem0 LLM provider; M3 uses infer=False so this is never consulted."""

    def __init__(self, config=None) -> None:
        self.config = config

    def generate_response(self, messages, tools=None, tool_choice="auto", **kwargs):
        return '{"facts": []}'


def build_local_mem0(runtime_dir):
    """Create an actual Mem0 OSS instance backed by local Qdrant."""

    import os
    from pathlib import Path

    root = Path(runtime_dir)
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MEM0_DIR", str(root / "config"))
    os.environ.setdefault("MEM0_TELEMETRY", "false")

    from mem0 import Memory
    from mem0.configs.llms.base import BaseLlmConfig
    from mem0.utils.factory import EmbedderFactory, LlmFactory

    # Mem0 1.0 validates provider names before consulting its extensible factories.
    # Rebind the accepted ``langchain`` slots to deterministic local providers so
    # this integration exercises Mem0/Qdrant without network credentials.
    EmbedderFactory.provider_to_class["langchain"] = "servicepilot.memory.mem0_local.HashEmbedding"
    LlmFactory.register_provider(
        "langchain",
        "servicepilot.memory.mem0_local.DeterministicLLM",
        BaseLlmConfig,
    )
    return Memory.from_config({
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "servicepilot_memory",
                "embedding_model_dims": 64,
                "path": str(root / "qdrant"),
                "on_disk": True,
            },
        },
        "embedder": {
            "provider": "langchain",
            "config": {"embedding_dims": 64},
        },
        "llm": {
            "provider": "langchain",
            "config": {"model": "deterministic-no-network"},
        },
        "history_db_path": str(root / "history.sqlite"),
        "version": "v1.1",
    })
