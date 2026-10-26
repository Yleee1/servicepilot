import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class FrozenDataTests(unittest.TestCase):
    def test_graph_v2_manifest_hash_count_and_mix(self):
        path = ROOT / "data/eval/graph_frozen_v2.jsonl"
        manifest = json.loads(
            (ROOT / "data/eval/graph_frozen_v2_manifest.json").read_text(encoding="utf-8")
        )
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest["sha256"])
        self.assertEqual(len(cases), manifest["case_count"])
        self.assertGreaterEqual(len(cases), 60)
        self.assertEqual(len({case["case_id"] for case in cases}), len(cases))
        categories = {case["category"] for case in cases}
        self.assertTrue({"compound", "approval", "restart", "cross_user"}.issubset(categories))
        self.assertEqual(
            manifest["review_status"],
            "independent_second_review_completed_user_attested",
        )

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

    def test_graph_sft_manifest_hashes_and_eval_separation(self):
        base = ROOT / "data/sft/graph_v2"
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["total"], 340)
        self.assertEqual(manifest["frozen_eval_message_overlap"], 0)
        self.assertEqual(set(manifest["split_input_intersections"].values()), {0})
        for split in ("train", "valid", "test"):
            path = base / f"{split}.jsonl"
            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), manifest["splits"][split]["count"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                manifest["splits"][split]["sha256"],
            )


if __name__ == "__main__":
    unittest.main()
