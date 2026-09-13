import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class FrozenDataTests(unittest.TestCase):
    def test_manifest_hash_and_unique_ids(self):
        path = ROOT / "data/eval/frozen_v1.jsonl"
        manifest = json.loads((ROOT / "data/eval/frozen_v1_manifest.json").read_text())
        content = path.read_bytes()
        cases = [json.loads(line) for line in content.decode().splitlines()]
        self.assertEqual(hashlib.sha256(content).hexdigest(), manifest["sha256"])
        self.assertEqual(len(cases), manifest["case_count"])
        self.assertEqual(len({case["case_id"] for case in cases}), len(cases))

    def test_frozen_inputs_do_not_exactly_overlap_sft_splits(self):
        frozen = {
            case["text"]
            for case in (
                json.loads(line) for line in (ROOT / "data/eval/frozen_v1.jsonl").read_text().splitlines()
            )
        }
        for split in ("train", "valid", "test"):
            messages = []
            for line in (ROOT / f"data/sft/v1/{split}.jsonl").read_text().splitlines():
                user = json.loads(json.loads(line)["messages"][1]["content"])["message"]
                messages.append(user)
            self.assertFalse(frozen.intersection(messages), split)


if __name__ == "__main__":
    unittest.main()
