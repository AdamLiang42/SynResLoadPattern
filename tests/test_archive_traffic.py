import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("archive", Path(__file__).parents[1] / "scripts/archive_traffic.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


def snapshot(day="2026-09-13", count=4, assets=None):
    return {"repository": "AdamLiang42/SynResLoadPattern", "retrieved_at": day + "T04:23:00Z",
            "traffic": {"clones": {"clones": [{"timestamp": day + "T00:00:00Z", "count": count, "uniques": 2}]},
                        "views": {"views": [{"timestamp": day + "T00:00:00Z", "count": 7, "uniques": 3}]}},
            "assets": [] if assets is None else assets}


def asset(identity=1, count=10, name="SynResLoadPattern.zip"):
    return {"id": identity, "tag": "v1.0.0", "name": name, "size": 123,
            "download_count": count, "download_url": "https://example.test/dataset.zip"}


class ArchiveTests(unittest.TestCase):
    def test_overlapping_window_replaces_counts_and_keeps_old_dates(self):
        old = archive.merge(None, snapshot("2026-08-01"))
        old = archive.merge(old, snapshot())
        new = archive.merge(old, snapshot(count=6))
        self.assertEqual(len(new["days"]), 2)
        self.assertEqual(new["days"]["2026-09-13"]["clones"], 6)
        self.assertEqual(old["days"]["2026-09-13"]["clones"], 4)

    def test_same_snapshot_is_idempotent(self):
        data = snapshot(assets=[asset()])
        state = archive.merge(None, data)
        self.assertEqual(archive.merge(state, data), state)

    def test_absent_metric_stays_unknown(self):
        data = snapshot()
        data["traffic"]["views"]["views"] = []
        state = archive.merge(None, data)
        self.assertNotIn("views", state["days"]["2026-09-13"])
        output = archive.render(state, data)
        self.assertIn("2026-09-13,,,4,2", output["traffic.csv"])
        self.assertIn("metrics inside that range: 2026-09-13", output["README.md"])

    def test_counts_are_not_summed_across_release_snapshots(self):
        old = archive.merge(None, snapshot(assets=[asset()]))
        new = archive.merge(old, snapshot(assets=[asset(count=12), asset(2, 50, "SHA256SUMS.txt")]))
        self.assertEqual(new["release_downloads"]["2026-09-13"], 12)

    def test_deleted_and_recreated_assets_preserve_previous_counter(self):
        old = archive.merge(None, snapshot(assets=[asset()]))
        new = archive.merge(old, snapshot("2026-09-14", assets=[asset(2, 3)]))
        self.assertFalse(new["assets"]["1"]["available"])
        self.assertEqual(new["release_downloads"]["2026-09-14"], 13)

    def test_stale_snapshot_cannot_replace_newer_data(self):
        old = archive.merge(None, snapshot("2026-09-14"))
        with self.assertRaises(ValueError):
            archive.merge(old, snapshot("2026-09-13"))

    def test_invalid_or_wrong_repository_data_fails(self):
        bad = snapshot(count=-1)
        with self.assertRaises(ValueError):
            archive.merge(None, bad)
        old = archive.merge(None, snapshot())
        old["repository"] = "someone/else"
        with self.assertRaises(ValueError):
            archive.merge(old, snapshot())

    def test_existing_branch_missing_state_is_not_reinitialized(self):
        with patch.object(archive, "api", side_effect=[{"object": {"sha": "parent"}},
                          {"tree": {"sha": "tree"}}, archive.APIError("HTTP 404")]):
            with self.assertRaises(archive.APIError):
                archive.get_previous("AdamLiang42/SynResLoadPattern", "traffic-data")

    def test_publish_preserves_tree_and_rejects_concurrent_writes(self):
        with patch.object(archive, "api", side_effect=[{"sha": "tree"}, {"sha": "commit"}]) as api:
            with patch.object(archive.subprocess, "run") as run:
                run.return_value.returncode = 1
                with self.assertRaises(archive.APIError):
                    archive.publish("owner/repo", "traffic-data", "parent", "base", {"state.json": "{}"}, "now")
                self.assertEqual(api.call_args_list[0].kwargs["payload"]["base_tree"], "base")
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"sha": "commit", "force": False})


if __name__ == "__main__":
    unittest.main()
