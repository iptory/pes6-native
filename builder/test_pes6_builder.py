"""Focused builder regression tests (no Tk, no network, no builds).

Run: python -m unittest discover -s builder -p "test_*.py"
"""

import io
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import pes6_builder as builder


class FormatPrereqReportTest(unittest.TestCase):
    def test_missing_listed_and_optional_pillow_noted(self):
        found = {
            "git": Path("C:/x/git.exe"), "cmake": None, "ninja": None,
            "clang": Path("C:/x/clang.exe"), "clangxx": None, "msvc": None, "rc": None,
        }
        report, missing = builder.format_prereq_report(found)
        self.assertEqual(missing, ["cmake", "ninja", "clangxx", "msvc", "rc"])
        self.assertIn("MISSING  cmake", report)
        self.assertIn("pillow", report.lower())


class DetectSourceRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tree_with_manifest(self, base):
        (base / "project").mkdir(parents=True)
        (base / "project" / "pes6_manifest.toml").write_text("[project]\n")
        return base

    def test_flat_archive_root(self):
        root = self._tree_with_manifest(self.tmp / "flat")
        self.assertEqual(builder.detect_source_root(root), root)

    def test_single_wrapper_directory(self):
        root = self._tree_with_manifest(self.tmp / "wrap" / "pes6-native-1.0.1")
        self.assertEqual(builder.detect_source_root(root), root)

    def test_missing_manifest_errors(self):
        (self.tmp / "empty").mkdir()
        with self.assertRaises(RuntimeError):
            builder.detect_source_root(self.tmp / "empty")

    def test_ambiguous_wrappers_error(self):
        self._tree_with_manifest(self.tmp / "amb" / "a")
        self._tree_with_manifest(self.tmp / "amb" / "b")
        with self.assertRaises(RuntimeError):
            builder.detect_source_root(self.tmp / "amb")


class MergeTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_overwrites_without_deleting_extras(self):
        src = self.tmp / "src"
        dst = self.tmp / "dst"
        (src / "project").mkdir(parents=True)
        (src / "project" / "new.txt").write_text("new")
        (dst / "project").mkdir(parents=True)
        (dst / "project" / "old.txt").write_text("old")
        (dst / "game").mkdir(parents=True)
        (dst / "game" / "kept.bin").write_text("keep")
        builder.merge_tree(src / "project", dst / "project")
        self.assertEqual((dst / "project" / "new.txt").read_text(), "new")
        self.assertEqual((dst / "project" / "old.txt").read_text(), "old")
        self.assertEqual((dst / "game" / "kept.bin").read_text(), "keep")

    def test_type_collision_raises(self):
        src = self.tmp / "src"
        dst = self.tmp / "dst"
        src.mkdir()
        (src / "project").write_text("file, not dir")
        (dst / "project").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            builder.merge_tree(src / "project", dst / "project")


class DownloadFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_response(self, payload, total=None):
        response = mock.Mock()
        response.getheader.return_value = None if total is None else str(total)
        response.read = mock.Mock(side_effect=[payload, b""])
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        return response

    def test_http_error_surfaces_release_hint(self):
        dest = self.tmp / "a.zip"
        with mock.patch.object(
            builder.urllib.request, "urlopen",
            side_effect=urllib.error.HTTPError("http://x", 404, "Not Found", {}, io.BytesIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                builder.download_file("http://x", dest)

    def test_content_length_mismatch_raises_and_cleans(self):
        dest = self.tmp / "b.zip"
        response = self._fake_response(b"short", total=100)
        with mock.patch.object(builder.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                builder.download_file("http://x", dest)
        self.assertFalse(dest.exists())

    def test_timeout_surfaces_stall_message(self):
        dest = self.tmp / "c.zip"
        with mock.patch.object(builder.urllib.request, "urlopen", side_effect=socket.timeout("t")):
            with self.assertRaisesRegex(RuntimeError, "timed out|stalled"):
                builder.download_file("http://x", dest)




class GitApplySafeTest(unittest.TestCase):
    def test_patch_can_be_retried_without_changing_the_result(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "file.txt").write_text("before\n", encoding="utf-8")
            patch = repo / "change.patch"
            patch.write_text(
                "diff --git a/file.txt b/file.txt\n"
                "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-before\n+after\n",
                encoding="utf-8",
            )
            builder.git_apply_safe("git", repo, patch)
            builder.git_apply_safe("git", repo, patch)
            self.assertEqual((repo / "file.txt").read_text(encoding="utf-8"), "after\n")

    def test_missing_patch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                builder.git_apply_safe("git", directory, Path(directory) / "missing.patch")


class RepairLibmspackStubsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stub_replaced_with_target_copy(self):
        stub_dir = self.tmp / "thirdparty" / "libmspack" / "cabextract" / "mspack"
        stub_dir.mkdir(parents=True)
        target = stub_dir / "real.c"
        target.write_bytes(b"x" * 300)
        (stub_dir / "link.c").write_bytes(b"real.c")
        fixed = builder.repair_libmspack_stubs(self.tmp)
        self.assertEqual(fixed, 1)
        self.assertEqual((stub_dir / "link.c").stat().st_size, 300)

    def test_missing_dir_returns_zero(self):
        self.assertEqual(builder.repair_libmspack_stubs(self.tmp / "nosdk"), 0)


class SyncGameDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rejects_folder_without_xex(self):
        src = self.tmp / "src"
        src.mkdir()
        with self.assertRaisesRegex(RuntimeError, "default.xex"):
            builder.sync_game_dir(src, self.tmp / "dst")

    def test_conflicting_game_file_is_not_overwritten(self):
        src = self.tmp / "src"
        dst = self.tmp / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "default.xex").write_bytes(b"new")
        (dst / "default.xex").write_bytes(b"old")
        with self.assertRaises(RuntimeError):
            builder.sync_game_dir(src, dst)
        self.assertEqual((dst / "default.xex").read_bytes(), b"old")

    def test_cancellation_does_not_leave_partial_game_file(self):
        src = self.tmp / "src"
        dst = self.tmp / "dst"
        src.mkdir()
        (src / "default.xex").write_bytes(b"game data")
        def cancel(*_args):
            raise builder.CancelledError("cancelled")
        with self.assertRaises(builder.CancelledError):
            builder.sync_game_dir(src, dst, on_progress=cancel)
        self.assertEqual(list(dst.iterdir()), [])


class ExtractorProgressHookTest(unittest.TestCase):
    def test_scan_reports_progress_without_changing_result(self):
        import xdvdfs_extract as x

        class FakeImage:
            size = 100

            def read_at(self, off, length):
                return b"\0" * length

        calls = []
        with mock.patch.object(x, "validate_descriptor", return_value=False):
            with self.assertRaises(RuntimeError):
                x.find_partition(
                    FakeImage(), on_progress=lambda d, t: calls.append((d, t)))
        self.assertTrue(calls)

    def test_scan_can_be_cancelled_from_progress_callback(self):
        import xdvdfs_extract as x

        class Image:
            size = 1
            def read_at(self, _offset, _length):
                return b"x"

        def cancel(*_args):
            raise builder.CancelledError("cancelled")

        with self.assertRaises(builder.CancelledError):
            x.find_partition(Image(), on_progress=cancel)


class StageRuntimeDllsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_build_outputs_raise(self):
        build = self.tmp / "build"
        build.mkdir()
        with self.assertRaisesRegex(RuntimeError, "did not produce"):
            builder.stage_runtime_dlls(build, self.tmp / "PES6", self.tmp / "sys")

    def test_stages_build_and_reports_missing_system(self):
        build = self.tmp / "build"
        build.mkdir()
        for name in builder.REQUIRED_BUILD_ARTIFACTS:
            (build / name).write_bytes(b"x")
        sysdir = self.tmp / "sys"
        sysdir.mkdir()
        (sysdir / "vcruntime140.dll").write_bytes(b"y")
        logs = []
        pes6 = self.tmp / "PES6"
        req, copied, missing = builder.stage_runtime_dlls(
            build, pes6, sysdir, log_fn=logs.append)
        self.assertTrue((pes6 / "pes6.exe").is_file())
        self.assertIn("vcruntime140.dll", copied)
        self.assertIn("msvcp140.dll", missing)
        self.assertTrue(any("NOTE" in line for line in logs))





class BundledSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prefers_exact_bundle_name(self):
        (self.tmp / "pes6-native-1.0.1.zip").write_text("old")
        exact = self.tmp / "pes6-native-source.zip"
        exact.write_text("new")
        self.assertEqual(builder.find_bundled_source([self.tmp]), exact)

    def test_none_when_absent(self):
        self.assertIsNone(builder.find_bundled_source([self.tmp / "missing"]))


class PrepareSourceBundledTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _archive(self, path, inner):
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr(f"{inner}/project/pes6_manifest.toml", "[project]\n")
            zipped.writestr(f"{inner}/patches/rexglue-sdk.patch", "patch")
            zipped.writestr(f"{inner}/tools/xdvdfs_extract.py", "# extractor\n")

    def test_bundled_wrapper_archive_staged_without_deleting_game(self):
        out = self.tmp / "out"
        bundle = self.tmp / "pes6-native-source.zip"
        self._archive(bundle, "pes6-native")
        (out / "game").mkdir(parents=True)
        (out / "game" / "keep.bin").write_text("keep")
        (out / "_source_extract").mkdir()
        (out / "_source_extract" / "user-file").write_text("keep")

        if not builder._TK_AVAILABLE:
            self.skipTest("Tk Builder unavailable here")
        app = builder.Builder.__new__(builder.Builder)
        import threading as _threading

        app.cancel_event = _threading.Event()
        app.report_stage = lambda m: None
        app.report_progress = lambda *a: None
        app.log = lambda m: None
        with mock.patch.object(builder, "find_bundled_source", return_value=bundle):
            project = builder.Builder.prepare_source(app, out)
        self.assertEqual(project, out / "project")
        self.assertTrue((out / "project" / "pes6_manifest.toml").is_file())
        self.assertEqual((out / "game" / "keep.bin").read_text(), "keep")
        self.assertEqual((out / "_source_extract" / "user-file").read_text(), "keep")



class DropValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_iso_suffix_accepted_without_touching_disk(self):
        from win32_drop import validate_dropped_paths
        missing = self.tmp / "game.iso"
        path, error = validate_dropped_paths([str(missing)])
        self.assertIsNone(error)
        self.assertEqual(path, missing)

    def test_folder_accepted(self):
        from win32_drop import validate_dropped_paths
        folder = self.tmp / "game"
        folder.mkdir()
        path, error = validate_dropped_paths([str(folder)])
        self.assertIsNone(error)
        self.assertEqual(path, folder)

    def test_multiple_drops_rejected(self):
        from win32_drop import validate_dropped_paths
        _path, error = validate_dropped_paths(["a.iso", "b.iso"])
        self.assertIsNotNone(error)
        self.assertIn("one", error.lower())

    def test_wrong_extension_rejected(self):
        from win32_drop import validate_dropped_paths
        _path, error = validate_dropped_paths([str(self.tmp / "notes.txt")])
        self.assertIsNotNone(error)
        self.assertIn(".iso", error)


class LaunchGameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_exe_reports_build_ok_but_no_launch(self):
        with self.assertRaisesRegex(RuntimeError, "missing"):
            builder.launch_game(self.tmp / "pes6.exe", self.tmp / "game", self.tmp / "Play PES6.cmd")

    def test_failed_start_reports_manual_fallback(self):
        exe = self.tmp / "pes6.exe"
        exe.write_bytes(b"x")
        with mock.patch.object(builder.subprocess, "Popen", side_effect=OSError("denied")):
            with self.assertRaisesRegex(RuntimeError, "manually"):
                builder.launch_game(exe, self.tmp / "game", self.tmp / "Play PES6.cmd")


class DropToBuildFlowTest(unittest.TestCase):
    def _app(self):
        app = builder.Builder.__new__(builder.Builder)
        app.msg_queue = queue.Queue()
        app.messages = []
        app._append_log = app.messages.append
        app.log = mock.Mock()
        app.worker = None
        app.iso = mock.Mock()
        app.iso.get.return_value = "  "
        app.iso.set = mock.Mock()
        app.stage_text = mock.Mock()
        app.detail_text = mock.Mock()
        app._set_indeterminate = lambda: None
        app.after = lambda *_args: None
        app.start = mock.Mock()
        return app


    def test_invalid_drop_shows_error_and_keeps_previous_source(self):
        app = self._app()
        app.iso.get.return_value = "C:/keep/game.iso"
        with mock.patch.object(builder.messagebox, "showerror") as showerror:
            builder.Builder.on_drop_files(app, ["notes.txt", "other.txt"])
        showerror.assert_called_once()
        app.iso.set.assert_not_called()
        app.start.assert_not_called()


    def test_licenses_missing_folder_explains_source_layout(self):
        app = self._app()
        app.log = mock.Mock()
        with mock.patch.object(builder, "licenses_dir", return_value=None):
            with mock.patch.object(builder.messagebox, "showinfo") as showinfo:
                builder.Builder.open_licenses(app)
        showinfo.assert_called_once()
        app.log.assert_called()


if __name__ == "__main__":
    unittest.main()
