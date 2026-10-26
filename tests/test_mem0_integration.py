import os
import tempfile
import unittest
from pathlib import Path

from servicepilot.memory import Mem0MemoryBackend, MemoryQuery, MemoryRecord


class Mem0IntegrationTests(unittest.TestCase):
    def test_local_mem0_add_search_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["MEM0_DIR"] = str(Path(directory) / "config")
            backend = Mem0MemoryBackend.local(Path(directory) / "runtime")
            record = MemoryRecord(
                subject_id="U001",
                memory_type="semantic",
                content="请用中文回复",
                scope_type="user",
                scope_id="U001",
                source_type="user_statement",
                source_ref="s1",
                confidence=1.0,
            )
            backend.add(record)
            hits = backend.search(MemoryQuery("U001", "中文回复", limit=3))
            self.assertEqual([hit.record.memory_id for hit in hits], [record.memory_id])
            backend.invalidate(record.memory_id, "test")
            self.assertEqual(backend.search(MemoryQuery("U001", "中文回复")), [])
            self.assertEqual(backend.get(record.memory_id).status, "deleted")

    def test_mem0_acl_excludes_other_user(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["MEM0_DIR"] = str(Path(directory) / "config")
            backend = Mem0MemoryBackend.local(Path(directory) / "runtime")
            for user, content in (("U001", "请用中文"), ("U002", "请用英文")):
                backend.add(MemoryRecord(
                    subject_id=user,
                    memory_type="semantic",
                    content=content,
                    scope_type="user",
                    scope_id=user,
                    source_type="user_statement",
                    source_ref="s",
                    confidence=1.0,
                ))
            hits = backend.search(MemoryQuery("U001", "回复语言"))
            self.assertEqual({hit.record.subject_id for hit in hits}, {"U001"})


if __name__ == "__main__":
    unittest.main()
