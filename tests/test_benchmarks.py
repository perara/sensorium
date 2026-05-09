import importlib.machinery
import importlib.util
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
BENCHMARKS_DIR = SCRIPTS_DIR / "benchmarks"
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sensorium.tools import compare_benchmarks, record_benchmark_artifact


class BenchmarkArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="sensorium-benchmark-test-"))

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_parse_single_benchmark_output(self):
        blocks = record_benchmark_artifact.parse_benchmark_output(
            [
                "Remote benchmark metrics:",
                "first_frame_latency_ms=467",
                "record_fps=6.42",
                "capture_rss_kb=15808",
            ]
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["metrics"]["first_frame_latency_ms"], 467)
        self.assertEqual(blocks[0]["metrics"]["record_fps"], 6.42)

    def test_parse_matrix_benchmark_output(self):
        blocks = record_benchmark_artifact.parse_benchmark_output(
            [
                "==> Benchmarking 10 fps",
                "sensor_target_fps=10",
                "record_fps=9.95",
                "==> Benchmarking 20 fps",
                "sensor_target_fps=20",
                "record_fps=19.42",
            ]
        )
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["label"], "Benchmarking 10 fps")
        self.assertEqual(blocks[1]["metrics"]["sensor_target_fps"], 20)

    def test_aggregate_sample_blocks_uses_median_metrics(self):
        blocks, sample_count = record_benchmark_artifact.aggregate_sample_blocks(
            [
                {"label": "sample 1/default", "metrics": {"record_fps": 9.0, "stream_width": 640}},
                {"label": "sample 2/default", "metrics": {"record_fps": 11.0, "stream_width": 640}},
                {"label": "sample 3/default", "metrics": {"record_fps": 10.0, "stream_width": 640}},
            ]
        )
        self.assertEqual(sample_count, 3)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["label"], "default")
        self.assertEqual(blocks[0]["metrics"]["record_fps"], 10.0)
        self.assertEqual(blocks[0]["metrics"]["stream_width"], 640)

    def test_aggregate_sample_blocks_passthrough_for_non_sample_labels(self):
        original = [{"label": "default", "metrics": {"record_fps": 9.0}}]
        blocks, sample_count = record_benchmark_artifact.aggregate_sample_blocks(original)
        self.assertIs(blocks, original)
        self.assertIsNone(sample_count)

    def test_compare_uses_latest_two_artifacts(self):
        older = self.tempdir / "a.json"
        newer = self.tempdir / "b.json"
        for path in (older, newer):
            path.write_text(json.dumps({"benchmarks": []}), encoding="utf-8")
        before, after = compare_benchmarks.latest_two_artifacts(self.tempdir)
        self.assertEqual(before, older)
        self.assertEqual(after, newer)

    def test_compare_uses_sensor_target_fps_as_key(self):
        entry = {"label": "Benchmarking 10 fps", "metrics": {"sensor_target_fps": 10}}
        self.assertEqual(compare_benchmarks.benchmark_key(entry, 0), "fps:10")

    def test_compare_fails_on_timestamp_fps_regression(self):
        before = self.tempdir / "before.json"
        after = self.tempdir / "after.json"
        before.write_text(
            json.dumps(
                {
                    "scenario": "default",
                    "benchmarks": [
                        {
                            "label": "default",
                            "metrics": {
                                "record_fps": 10.0,
                                "timestamp_fps": 10.0,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        after.write_text(
            json.dumps(
                {
                    "scenario": "default",
                    "benchmarks": [
                        {
                            "label": "default",
                            "metrics": {
                                "record_fps": 9.8,
                                "timestamp_fps": 8.0,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(BENCHMARKS_DIR / "compare-benchmarks.py"),
                str(before),
                str(after),
                "--fail-on-regression",
                "--prefer-sustained-rate",
                "--min-timestamp-fps-ratio",
                "0.95",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timestamp_fps ratio", result.stdout)

    def test_default_benchmark_dir_is_repo_cache(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                compare_benchmarks.default_benchmark_dir(),
                REPO_ROOT / ".cache" / "benchmarks",
            )
            self.assertEqual(
                record_benchmark_artifact.default_benchmark_dir(),
                REPO_ROOT / ".cache" / "benchmarks",
            )

    def test_default_benchmark_dir_honors_env_override(self):
        custom = self.tempdir / "benchmarks"
        with mock.patch.dict(os.environ, {"SENSORIUM_BENCHMARK_DIR": str(custom)}):
            self.assertEqual(compare_benchmarks.default_benchmark_dir(), custom)
            self.assertEqual(record_benchmark_artifact.default_benchmark_dir(), custom)

    def test_compare_allows_missing_default_baseline(self):
        result = subprocess.run(
            [
                sys.executable,
                str(BENCHMARKS_DIR / "compare-benchmarks.py"),
                "--artifact-dir",
                str(self.tempdir),
                "--allow-missing-baseline",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("benchmark baseline unavailable", result.stdout)


if __name__ == "__main__":
    unittest.main()
