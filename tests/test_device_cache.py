from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab5_flasher.device_cache import prepare_target_cache
from lab5_flasher.portable import CancellationToken


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()


class FakeGitHubClient:
    files = {
        "boot.bin": b"boot-v1",
        "app.bin": b"app-v1",
    }
    downloads = 0

    def latest_commit_for_path(self, repo, branch, path):
        return {"sha": "a" * 40}

    def directory(self, repo, ref, path):
        return [
            {"name": name, "type": "file", "size": len(data), "sha": blob_sha(data)}
            for name, data in self.files.items()
        ]

    def download_content(self, repo, ref, full_path, destination, *, progress=None, cancelled=None):
        type(self).downloads += 1
        data = self.files[Path(full_path).name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        if progress:
            progress(len(data), len(data))


class DeviceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeGitHubClient.files = {"boot.bin": b"boot-v1", "app.bin": b"app-v1"}
        FakeGitHubClient.downloads = 0
        self.target = {
            "enabled": True,
            "name": "test",
            "chip": "esp32c5",
            "source": {"type": "github", "repo": "example/test", "branch": "main", "path": "latest"},
            "flash": [
                {"offset": "0x2000", "file": "boot.bin"},
                {"offset": "0x20000", "file": "app.bin"},
            ],
        }

    @mock.patch("lab5_flasher.device_cache.GitHubClient", FakeGitHubClient)
    def test_cache_downloads_once_then_only_changed_blob(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args = ("test", self.target, CancellationToken(), lambda *_: None, lambda *_: None, root)
            first = prepare_target_cache(*args)
            self.assertEqual(first.updated, 2)
            self.assertEqual(FakeGitHubClient.downloads, 2)
            second = prepare_target_cache(*args)
            self.assertEqual(second.updated, 0)
            self.assertEqual(FakeGitHubClient.downloads, 2)
            FakeGitHubClient.files["app.bin"] = b"app-v2"
            third = prepare_target_cache(*args)
            self.assertEqual(third.updated, 1)
            self.assertEqual(FakeGitHubClient.downloads, 3)
            self.assertEqual((third.directory / "app.bin").read_bytes(), b"app-v2")
            self.assertEqual((third.directory / "boot.bin").read_bytes(), b"boot-v1")


if __name__ == "__main__":
    unittest.main()
