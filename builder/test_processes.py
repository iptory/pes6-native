import ctypes
import os
import subprocess
import sys
import unittest
from ctypes import wintypes

from processes import ProcessTree


@unittest.skipUnless(os.name == "nt", "Windows job ownership")
class ProcessTreeTest(unittest.TestCase):
    def test_child_is_terminated_even_after_parent_exits(self):
        code = (
            "import subprocess,sys; "
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            "print(child.pid,flush=True)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, creationflags=0x08000004,
        )
        tree = None
        child_handle = None
        kernel = ctypes.WinDLL("kernel32")
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            tree = ProcessTree(process)
            output, error = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, error)
            child_handle = kernel.OpenProcess(0x100000, False, int(output.strip()))
            self.assertTrue(child_handle)
            self.assertEqual(kernel.WaitForSingleObject(child_handle, 0), 258)
            tree.terminate()
            self.assertEqual(kernel.WaitForSingleObject(child_handle, 5000), 0)
        finally:
            if tree:
                tree.close()
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if child_handle:
                kernel.CloseHandle(child_handle)


if __name__ == "__main__":
    unittest.main()
