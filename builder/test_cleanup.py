"""Exercise real cleanup subprocesses without touching installed tools or games."""
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

import pes6_builder as builder


@unittest.skipUnless(os.name == "nt", "Windows installer behavior")
class DownloadedInstallerCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "download André's.exe"
        self.target.write_bytes(b"owned installer stand-in")
        self.sibling = self.root / "keep.iso"
        self.sibling.write_bytes(b"user input")
        self.process = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                           ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                           ctypes.c_void_p]
        self.kernel.CreateFileW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]

    def tearDown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        self.temp.cleanup()

    def schedule(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(self.target)):
            self.process = builder.remove_installer_after_exit()
        return self.process

    def lock_against_deletion(self):
        handle = self.kernel.CreateFileW(str(self.target), 0x80000000, 3, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def test_source_mode_never_removes_interpreter_path(self):
        with mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.object(sys, "executable", str(self.target)):
            self.process = builder.remove_installer_after_exit()
        self.assertIsNone(self.process)
        self.assertEqual(self.target.read_bytes(), b"owned installer stand-in")

    def test_busy_installer_is_removed_after_release_without_touching_siblings(self):
        handle = self.lock_against_deletion()
        try:
            process = self.schedule()
            self.assertEqual(self.target.read_bytes(), b"owned installer stand-in")
        finally:
            self.kernel.CloseHandle(handle)
        self.assertEqual(process.wait(timeout=15), 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.sibling.read_bytes(), b"user input")

    def test_replaced_download_is_preserved(self):
        handle = self.lock_against_deletion()
        try:
            process = self.schedule()
            self.target.write_bytes(b"a replacement supplied by the user")
        finally:
            self.kernel.CloseHandle(handle)
        self.assertEqual(process.wait(timeout=15), 1)
        self.assertEqual(self.target.read_bytes(), b"a replacement supplied by the user")
        self.assertEqual(self.sibling.read_bytes(), b"user input")


@unittest.skipUnless(os.name == "nt", "Windows process ownership")
class InstallationCancellationTest(unittest.TestCase):
    def exercise(self, cancellable):
        with tempfile.TemporaryDirectory() as directory:
            completed = Path(directory) / "completed"
            app = builder.Builder.__new__(builder.Builder)
            app.cancel_event = threading.Event()
            app._process_lock = threading.Lock()
            app._process_tree = None
            app._process_cancellable = True
            app.process = None
            app.report_stage = lambda *_args: None
            app.report_progress = lambda *_args: None
            app.log = lambda line: app.cancel() if line == "START" else None
            code = ("from pathlib import Path; import time; "
                    "print('START', flush=True); time.sleep(0.2); "
                    f"Path({str(completed)!r}).write_text('finished safely')")
            with self.assertRaises(builder.CancelledError):
                app.run([sys.executable, "-c", code], cancellable=cancellable)
            return completed.read_text() if completed.exists() else None

    def test_portable_build_cancellation_terminates_owned_work(self):
        self.assertIsNone(self.exercise(True))

    def test_shared_component_finishes_safely_before_cancellation(self):
        self.assertEqual(self.exercise(False), "finished safely")


if __name__ == "__main__":
    unittest.main()
