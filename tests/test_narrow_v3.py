import json
import tempfile
import unittest
from pathlib import Path

from servicepilot.memory_ablation_v3 import prepare_data
from servicepilot.narrow_sft_v3 import generate


class NarrowV3DataTests(unittest.TestCase):
    def test_narrow_splits_have_no_exact_input_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = generate(Path(directory), records_per_task=24)
            for task in manifest["tasks"].values():
                self.assertEqual(set(task["split_input_intersections"].values()), {0})

    def test_memory_manifest_hashes_match_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = prepare_data(root)
            saved = json.loads((root / "manifest.json").read_text())
            self.assertEqual(saved, manifest)
            self.assertEqual({item["count"] for item in manifest["splits"].values()}, {14})


if __name__ == "__main__":
    unittest.main()
