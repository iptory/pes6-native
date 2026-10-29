"""Tests for builder.installation (no Tk, no network, no builds).

Run: python -m unittest discover -s builder -p "test_*.py"
"""

import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import installation as inst


def _can_symlink(tmp: Path) -> bool:
    probe = tmp / "probe-target"
    probe.mkdir(exist_ok=True)
    link = tmp / "probe-link"
    try:
        link.symlink_to(probe, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    try:
        return link.is_symlink()
    finally:
        try:
            if link.is_symlink():
                link.unlink()
        except OSError:
            pass


class SessionLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_context_cleans_workspace_only(self):
        out = self.tmp / "out"
        sibling = self.tmp / "keep.txt"
        sibling.write_text("user file")
        with inst.BuildSession(out) as session:
            workspace = session.workspace
            self.assertEqual(session.staging, workspace / "PES6")
            self.assertEqual(session.destination, session.output / "PES6")
            self.assertEqual(workspace.parent, session.output)
            (workspace / "sdk").mkdir(parents=True)
            (workspace / "sdk" / "clone.bin").write_bytes(b"x" * 16)
        self.assertFalse(os.path.lexists(workspace))
        self.assertTrue(out.is_dir())
        self.assertEqual(sibling.read_text(), "user file")

    def test_failure_inside_context_still_cleans(self):
        out = self.tmp / "out"
        with self.assertRaises(RuntimeError):
            with inst.BuildSession(out) as session:
                workspace = session.workspace
                (workspace / "build").mkdir()
                raise RuntimeError("compile boom")
        self.assertFalse(os.path.lexists(workspace))

    def test_failed_close_leaves_session_open_for_retry(self):
        session = inst.BuildSession(self.tmp / "out")
        workspace = session.workspace
        with mock.patch.object(
            inst.shutil, "rmtree", side_effect=[OSError("locked"), None]
        ):
            with self.assertRaises(inst.WorkspaceCleanupError):
                session.close()
            self.assertFalse(session.closed)
            session.close()  # retry once the process tree is gone
            self.assertTrue(session.closed)
        self.assertTrue(os.path.lexists(workspace))  # mock never deleted
        shutil.rmtree(workspace, ignore_errors=True)

    def test_closed_session_cannot_publish(self):
        session = inst.BuildSession(self.tmp / "out")
        session.close()
        with self.assertRaises(inst.InstallationError):
            session.publish()


class PublishSuccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source_root = self.tmp / "repo"
        self.source_root.mkdir()
        (self.source_root / "THIRD_PARTY_NOTICES.md").write_text("notices")
        (self.source_root / "LICENSE").write_text("license")
        (self.source_root / "builder").mkdir()
        (self.source_root / "builder" / "NOTICES.md").write_text("builder-only")

    def _stage_realistic_game(self, staging: Path, final_game: Path):
        staging.mkdir()
        for name in ("pes6.exe", "rexruntime.dll", "rexgpu-xenos.dll",
                     "msvcp140.dll", "vcruntime140.dll"):
            (staging / name).write_bytes(b"\x4d\x5a" + name.encode())
        game = staging / "game"
        (game / "content" / "league").mkdir(parents=True)
        (game / "default.xex").write_bytes(b"XEX" + b"\x00" * 64)
        (game / "content" / "league" / "teams.dat").write_bytes(b"teams" * 100)
        (game / "save edit.dat").write_bytes(b"user-ish bytes")
        inst.write_runtime_config(staging, final_game)
        inst.write_launcher(staging)
        (staging / "title.ico").write_bytes(b"ICO")
        inst.copy_notices(self.source_root, staging)

    def test_success_final_inventory_unicode_spaces(self):
        out = self.tmp / "PES6 saída \U00010437 build"
        with inst.BuildSession(out) as session:
            self._stage_realistic_game(session.staging, session.destination / "game")
            published = session.publish()
            self.assertEqual(published, session.destination)
            session.close()
            self.assertFalse(os.path.lexists(session.workspace))

        final = out / "PES6"
        names = {child.name for child in final.iterdir()}
        self.assertEqual(names, {
            "pes6.exe", "rexruntime.dll", "rexgpu-xenos.dll",
            "msvcp140.dll", "vcruntime140.dll",
            "pes6.toml", "Play PES6.cmd", "title.ico",
            "THIRD_PARTY_NOTICES.md", "LICENSE", "game",
        })
        for child in final.rglob("*"):
            self.assertFalse(child.name.startswith(".pes6-"))
            self.assertNotIn(child.suffix.lower(), (".log", ".part", ".tmp"))
            self.assertNotEqual(child.name, "game_manifest.json")
            self.assertNotEqual(child.name, "CMakeCache.txt")
        self.assertTrue((final / "game" / "default.xex").is_file())
        self.assertTrue((final / "game" / "content" / "league" / "teams.dat").is_file())
        self.assertTrue((final / "game" / "save edit.dat").is_file())
        config = tomllib.loads((final / "pes6.toml").read_text(encoding="utf-8"))
        self.assertEqual(Path(config["game_data_root"]), final / "game")
        self.assertFalse(config["log_to_file"])
        raw = (final / "Play PES6.cmd").read_bytes()
        raw.decode("ascii")
        text = raw.decode("utf-8")
        self.assertIn("%~dp0pes6.exe", text)
        self.assertIn("%~dp0game", text)
        self.assertNotIn(str(final), text)

    def test_publish_into_empty_destination_reuses_it(self):
        out = self.tmp / "out"
        (out / "PES6").mkdir(parents=True)
        with inst.BuildSession(out) as session:
            self._stage_realistic_game(session.staging, session.destination / "game")
            published = session.publish()
        self.assertEqual(published, out / "PES6")
        self.assertTrue((published / "pes6.exe").is_file())

    def test_publish_without_staging_fails(self):
        session = inst.BuildSession(self.tmp / "out")
        try:
            with self.assertRaises(inst.InstallationError):
                session.publish()
        finally:
            session.close()


class DestinationCollisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _session_with_staged_game(self, out: Path):
        session = inst.BuildSession(out)
        self.addCleanup(session.close)
        (session.staging / "game").mkdir(parents=True)
        (session.staging / "game" / "default.xex").write_bytes(b"XEX")
        inst.write_runtime_config(session.staging, session.destination / "game")
        inst.write_launcher(session.staging)
        return session

    def test_nonempty_destination_refused_and_preserved(self):
        out = self.tmp / "out"
        (out / "PES6" / "saves").mkdir(parents=True)
        (out / "PES6" / "pes6.toml").write_text("user settings")
        (out / "PES6" / "saves" / "career.sav").write_bytes(b"career")
        session = self._session_with_staged_game(out)
        with self.assertRaises(inst.DestinationExistsError) as raised:
            session.publish()
        self.assertEqual(raised.exception.destination, out / "PES6")
        self.assertEqual((out / "PES6" / "pes6.toml").read_text(), "user settings")
        self.assertEqual((out / "PES6" / "saves" / "career.sav").read_bytes(), b"career")
        self.assertFalse((out / "PES6" / "game").exists())
        self.assertTrue(session.staging.is_dir())

    def test_early_check_fails_before_any_build_work(self):
        out = self.tmp / "out"
        (out / "PES6" / "saves").mkdir(parents=True)
        (out / "PES6" / "pes6.toml").write_text("user settings")
        session = inst.BuildSession(out)
        self.addCleanup(session.close)
        self.assertFalse(session.staging.exists())
        with self.assertRaises(inst.DestinationExistsError):
            session.check_destination_free()
        self.assertEqual((out / "PES6" / "pes6.toml").read_text(), "user settings")

    def test_destination_file_refused(self):
        out = self.tmp / "out"
        out.mkdir(parents=True)
        (out / "PES6").write_text("not a directory")
        session = self._session_with_staged_game(out)
        with self.assertRaises(inst.DestinationExistsError):
            session.publish()
        self.assertEqual((out / "PES6").read_text(), "not a directory")

    def test_symlink_destination_refused(self):
        if not _can_symlink(self.tmp):
            self.skipTest("symlinks need privileges on this machine")
        out = self.tmp / "out"
        out.mkdir()
        real = self.tmp / "real-target"
        real.mkdir()
        (real / "precious.txt").write_text("do not touch")
        link = out / "PES6"
        link.symlink_to(real, target_is_directory=True)
        session = inst.BuildSession(out)
        self.addCleanup(session.close)
        (session.staging / "game").mkdir(parents=True)
        with self.assertRaises(inst.DestinationExistsError):
            session.publish()
        self.assertTrue(link.is_symlink())
        self.assertEqual((real / "precious.txt").read_text(), "do not touch")


class NoticesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_copy_notices_only_game_notices(self):
        root = self.tmp / "repo"
        (root / "builder").mkdir(parents=True)
        (root / "THIRD_PARTY_NOTICES.md").write_text("notices")
        (root / "LICENSE").write_text("license")
        (root / "builder" / "NOTICES.md").write_text("builder-only")
        staging = self.tmp / "staging"
        self.assertEqual(inst.copy_notices(root, staging),
                         ["THIRD_PARTY_NOTICES.md", "LICENSE"])
        self.assertFalse((staging / "builder").exists())
        self.assertFalse((staging / "NOTICES.md").exists())

    def test_copy_notices_accepts_frozen_bundle_layout(self):
        frozen = self.tmp / "bundle"
        (frozen / "licenses").mkdir(parents=True)
        (frozen / "licenses" / "PROJECT.txt").write_text("project license")
        (frozen / "licenses" / "THIRD_PARTY_NOTICES.md").write_text("third party")
        staging = self.tmp / "frozen-staging"
        self.assertEqual(inst.copy_notices(frozen, staging),
                         ["THIRD_PARTY_NOTICES.md", "LICENSE"])
        self.assertEqual((staging / "LICENSE").read_text(), "project license")

    def test_copy_notices_missing_source_errors(self):
        with self.assertRaisesRegex(inst.InstallationError, "THIRD_PARTY_NOTICES"):
            inst.copy_notices(self.tmp / "empty-root", self.tmp / "staging")


class CleanupSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_cleanup_failure_reports_exact_path(self):
        session = inst.BuildSession(self.tmp / "out")
        workspace = session.workspace
        with mock.patch.object(
            inst.shutil, "rmtree", side_effect=OSError("locked by game")
        ):
            with self.assertRaises(inst.WorkspaceCleanupError) as raised:
                session.close()
        self.assertEqual(raised.exception.workspace, workspace)
        self.assertIn(str(workspace), str(raised.exception))
        self.assertTrue(os.path.lexists(workspace))
        self.assertFalse(session.closed)  # retryable once the tree is gone

    def test_cleanup_failure_preserves_published_game(self):
        session = inst.BuildSession(self.tmp / "out")
        session.staging.mkdir()
        (session.staging / "pes6.exe").write_bytes(b"MZ")
        published = session.publish()
        with mock.patch.object(
            inst.shutil, "rmtree", side_effect=OSError("locked")
        ):
            with self.assertRaises(inst.WorkspaceCleanupError) as raised:
                session.close()
        self.assertEqual(raised.exception.published, published)
        self.assertTrue((published / "pes6.exe").is_file())
        inst.shutil.rmtree(session.workspace, ignore_errors=True)

    def test_cleanup_retries_read_only_workspace_files(self):
        session = inst.BuildSession(self.tmp / "out")
        workspace = session.workspace
        packed = workspace / "repo" / "objects" / "pack"
        packed.mkdir(parents=True)
        blob = packed / "pack-1.pack"
        blob.write_bytes(b"git-objects")
        try:
            os.chmod(blob, 0o444)
        except OSError:
            self.skipTest("cannot set read-only bit here")
        session.close()
        self.assertTrue(session.closed)
        self.assertFalse(os.path.lexists(workspace))

    @unittest.skipUnless(os.name == "nt", "Windows long worktree paths")
    def test_long_read_only_worktree_is_removed_without_touching_siblings(self):
        session = inst.BuildSession(self.tmp / "out")
        sibling = session.output / "existing-save.dat"
        sibling.write_bytes(b"user save")
        deep = session.workspace.joinpath(*(["nested-component-" * 3] * 6))
        extended = "\\\\?\\" + str(deep)
        os.makedirs(extended)
        packed = os.path.join(extended, "pack-readonly.pack")
        with open(packed, "wb") as output:
            output.write(b"git objects")
        os.chmod(packed, 0o444)
        session.close()
        self.assertFalse(session.workspace.exists())
        self.assertEqual(sibling.read_bytes(), b"user save")

    def test_cleanup_never_follows_special_workspace(self):
        session = inst.BuildSession(self.tmp / "out")
        workspace = session.workspace
        session.close()
        outside = self.tmp / "outside.txt"
        outside.write_text("precious")
        try:
            workspace.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges on this machine")
        victim = inst.BuildSession(self.tmp / "out2")
        victim._workspace = workspace
        with self.assertRaises(inst.WorkspaceCleanupError):
            victim.close()
        self.assertEqual(outside.read_text(), "precious")
        workspace.unlink()


if __name__ == "__main__":
    unittest.main()
