import tempfile
import unittest
from pathlib import Path

from modules.version import current_version

COMMIT = "7cc2b22a1b2c3d4e5f60718293a4b5c6d7e8f901"


class VersionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".git" / "refs" / "heads").mkdir(parents=True)

    def test_reads_loose_ref_packed_ref_and_detached_head(self):
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (self.root / ".git" / "refs" / "heads" / "main").write_text(COMMIT + "\n")
        self.assertEqual(current_version(self.root), "7cc2b22")
        (self.root / ".git" / "refs" / "heads" / "main").unlink()
        (self.root / ".git" / "packed-refs").write_text(
            f"# pack-refs\n{COMMIT} refs/heads/main\n0000000000 refs/heads/other\n")
        self.assertEqual(current_version(self.root), "7cc2b22")
        (self.root / ".git" / "HEAD").write_text(COMMIT + "\n")
        self.assertEqual(current_version(self.root), "7cc2b22")

    def test_missing_or_unreadable_repository_gives_none(self):
        self.assertIsNone(current_version(self.root / "nowhere"))
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/gone\n")
        self.assertIsNone(current_version(self.root))

    def test_this_repository_has_a_version(self):
        self.assertRegex(current_version() or "", r"^[0-9a-f]{7}$")


if __name__ == "__main__":
    unittest.main()
