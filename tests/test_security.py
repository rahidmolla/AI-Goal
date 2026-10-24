import os
import tempfile
import unittest
from pathlib import Path

from taskpilot.security import resolve_path


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_normal_relative_path_inside_workspace(self):
        self.assertEqual(resolve_path(self.root, "notes/a.txt"), self.root / "notes" / "a.txt")

    def test_rejects_absolute_and_parent_traversal_paths(self):
        for candidate in ("/etc/passwd", "../outside.txt", "notes/../../outside.txt", ""):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                resolve_path(self.root, candidate)

    def test_rejects_symlink_that_escapes_workspace(self):
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("do not touch")
        link = self.root / "escape"
        try:
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                resolve_path(self.root, "escape/new.txt")
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_symlinks_even_when_they_resolve_inside_workspace(self):
        real = self.root / "real.txt"
        real.write_text("keep safe")
        (self.root / "alias.txt").symlink_to(real)
        with self.assertRaises(ValueError):
            resolve_path(self.root, "alias.txt")

    def test_rejects_hard_linked_files(self):
        original = self.root / "original.txt"
        original.write_text("outside reference")
        (self.root / "linked.txt").hardlink_to(original)
        with self.assertRaises(ValueError):
            resolve_path(self.root, "linked.txt")
        self.assertEqual(original.read_text(), "outside reference")

    def test_rejects_symlinked_parent_components(self):
        outside = self.root.parent / (self.root.name + "-outside-dir")
        outside.mkdir()
        try:
            (self.root / "alias-dir").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_path(self.root, "alias-dir/new.txt")
        finally:
            (self.root / "alias-dir").unlink(missing_ok=True)
            outside.rmdir()

    def test_rejects_backslash_parent_traversal(self):
        with self.assertRaises(ValueError):
            resolve_path(self.root, r"..\\escape.txt")

    def test_rejects_windows_absolute_paths(self):
        with self.assertRaises(ValueError):
            resolve_path(self.root, r"C:\\Windows\\secret.txt")


if __name__ == "__main__":
    unittest.main()
