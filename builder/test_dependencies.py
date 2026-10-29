"""Tests for builder/dependencies.py (no Tk, network, or OS installers).

The parent ``run`` contract: ``run(command, cwd=None,
allowed_exit_codes=(0,), cancellable=True)`` returns combined output text
and raises other exits. Fakes assert the tuple keyword and the
cancellable flag. ``download`` fakes write real (tiny) archives so the
production extraction and traversal checks run for real; SHA-256 is
patched to no-op on success paths and exercised for real on mismatch.
"""

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dependencies as deps


def _write_zip(dest, members):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in members.items():
            zipped.writestr(name, data)


def _write_tar_xz(dest, members):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:xz") as tarred:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tarred.addfile(info, io.BytesIO(data))


MS_SUBJECT = ("CN=Microsoft Corporation, O=Microsoft Corporation, "
              "L=Redmond, S=Washington, C=US")


def make_run(calls=None, versions=None, vswhere=None, sig_status="Valid",
             sig_subject=MS_SUBJECT, installer_output="done",
             installer_error=None):
    """Fake parent runner asserting the allowed_exit_codes contract."""
    versions = versions or {}

    def fake_run(command, cwd=None, allowed_exit_codes=(0,), cancellable=True):
        assert isinstance(allowed_exit_codes, tuple), \
            "run must be called with an allowed_exit_codes tuple"
        if calls is not None:
            calls.append((list(command), cwd, allowed_exit_codes, cancellable))
        exe = str(command[0])
        lowered = exe.lower().replace("\\", "/")
        base = lowered.rsplit("/", 1)[-1]
        joined = " ".join(str(part) for part in command)
        if "vswhere" in base:
            if isinstance(vswhere, Exception):
                raise vswhere
            return vswhere if vswhere is not None else "[]"
        if "get-authenticodesignature" in joined.lower():
            assert base == "powershell.exe" and os.path.isabs(exe), \
                "signature probe must use the absolute system powershell.exe"
            assert cancellable is True
            return json.dumps({"Status": sig_status, "Subject": sig_subject})
        if "start-process" in joined.lower():
            if installer_error is not None:
                raise installer_error
            assert allowed_exit_codes == (0, 3010), \
                "Microsoft installers must tolerate documented 3010"
            assert cancellable is False, \
                "shared install must finish before cancellation applies"
            return installer_output
        for name, output in versions.items():
            if base == name:
                return output
        return ""

    return fake_run


def make_download(calls=None, fail=None):
    """Fake parent atomic downloader writing tiny real archives."""
    def fake_download(url, dest):
        if calls is not None:
            calls.append((url, str(dest)))
        if fail is not None:
            raise fail
        dest = Path(dest)
        if "MinGit" in url:
            _write_zip(dest, {"cmd/git.exe": b"fake-git",
                              "cmd/git-remote-https.exe": b"x"})
        elif "cmake-" in url:
            _write_zip(dest, {"cmake-4.4.5-windows-x86_64/bin/cmake.exe": b"fake-cmake"})
        elif "ninja" in url.lower():
            _write_zip(dest, {"ninja.exe": b"fake-ninja"})
        elif "llvm" in url.lower():
            _write_tar_xz(dest, {"llvmroot/bin/clang.exe": b"fake-clang",
                                 "llvmroot/bin/clang++.exe": b"fake-clangxx"})
        elif "vs_BuildTools" in url or "vc_redist" in url:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake-signed-installer")
        else:
            raise AssertionError(f"unexpected download url: {url}")
    return fake_download


GOOD_VERSIONS = {
    "git.exe": "git version 2.56.0.windows.4",
    "cmake.exe": "cmake version 4.4.5",
    "ninja.exe": "1.13.2",
    "clang.exe": "clang version 23.1.4",
    "clang++.exe": "clang version 23.1.4",
}

FLOOR_VERSIONS = {
    "git.exe": "git version 2.0.0",
    "cmake.exe": "cmake version 3.25.0",
    "ninja.exe": "1.11.0",
    "clang.exe": "clang version 18.0.0",
    "clang++.exe": "clang version 18.0.0",
}


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "work"
        self.saved_path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self.saved_path
        self.tmp.cleanup()

    def _pair(self, name="shared"):
        link = (self.workspace / name / "2022" / "VC" / "Tools" / "MSVC"
                / "14.44.0" / "bin" / "Hostx64" / "x64" / "link.exe")
        rc = self.workspace / name / "rc.exe"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_bytes(b"link")
        rc.write_bytes(b"rc")
        return link, rc


class VersionBoundariesTest(BaseCase):
    def _write_old(self, name):
        old = self.workspace / "old" / name
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old")
        return old

    def test_floor_versions_are_accepted_without_downloads(self):
        link, rc = self._pair()
        paths = {key: str(self._write_old("clang++.exe" if key == "clangxx" else f"{key}.exe"))
                 for key in ("git", "cmake", "ninja", "clang", "clangxx")}
        paths["msvc"] = str(link)
        paths["rc"] = str(rc)
        download_calls = []
        run_calls = []
        with mock.patch.object(deps, "_resolve_msvc", return_value=(link, rc)), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]):
            deps.ensure_tools(
                self.workspace, paths,
                download=make_download(calls=download_calls),
                run=make_run(calls=run_calls,
                             versions=dict(FLOOR_VERSIONS)),
                log=lambda _m: None, progress=lambda *a: None,
                cancelled=lambda: False)
        self.assertEqual(download_calls, [])

    def test_old_cmake_and_clang_are_reinstalled_portable(self):
        old_cmake = self.workspace / "old" / "cmake.exe"
        old_cmake.parent.mkdir(parents=True)
        old_cmake.write_bytes(b"old")
        old_clang = self.workspace / "old" / "clang.exe"
        old_clang.write_bytes(b"old")
        old_clangxx = self.workspace / "old" / "clang++.exe"
        old_clangxx.write_bytes(b"old")
        link, rc = self._pair()
        versions = dict(GOOD_VERSIONS)
        versions["cmake.exe"] = "cmake version 3.22.1"
        versions["clang.exe"] = "clang version 17.0.6"
        versions["clang++.exe"] = "clang version 17.0.6"

        def run(command, cwd=None, allowed_exit_codes=(0,), cancellable=True):
            assert isinstance(allowed_exit_codes, tuple)
            base = str(command[0]).lower().replace("\\", "/").rsplit("/", 1)[-1]
            text = str(command[0])
            if text == str(old_cmake):
                return versions["cmake.exe"]
            if text in (str(old_clang), str(old_clangxx)):
                return versions["clang.exe"]
            if base in GOOD_VERSIONS:
                return GOOD_VERSIONS[base]
            return ""

        download_calls = []
        with mock.patch.object(deps, "_resolve_msvc", return_value=(link, rc)), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            result = deps.ensure_tools(
                self.workspace,
                {"git": None, "cmake": str(old_cmake), "ninja": None,
                 "clang": str(old_clang), "clangxx": str(old_clangxx),
                 "msvc": str(link), "rc": str(rc)},
                download=make_download(calls=download_calls), run=run,
                log=lambda _m: None, progress=lambda *a: None,
                cancelled=lambda: False)
        self.assertNotEqual(str(result["cmake"]), str(old_cmake))
        self.assertNotEqual(str(result["clang"]), str(old_clang))
        urls = [url for url, _dest in download_calls]
        self.assertTrue(any("cmake-" in url for url in urls))
        self.assertTrue(any("llvm" in url.lower() for url in urls))


class FreshEmptyInventoryTest(BaseCase):
    def test_empty_inventory_bootstraps_portable_without_system_mutation(self):
        link, rc = self._pair()
        run = make_run(versions=dict(GOOD_VERSIONS))
        with mock.patch.object(deps, "_resolve_msvc", return_value=(link, rc)), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            result = deps.ensure_tools(
                self.workspace, {key: None for key in deps.TOOL_KEYS},
                download=make_download(), run=run,
                log=lambda _m: None, progress=lambda *_args: None,
                cancelled=lambda: False)
        tools_root = (self.workspace / "tools").resolve()
        for key in ("git", "cmake", "ninja", "clang", "clangxx"):
            resolved = str(Path(result[key]).resolve())
            self.assertTrue(resolved.startswith(str(tools_root) + os.sep),
                            f"{key} must live under workspace/tools")
        for key in ("git", "cmake", "ninja", "clang", "clangxx"):
            self.assertTrue(Path(result[key]).is_file())


class VsInstallTest(BaseCase):
    def test_vs2019_triggers_elevated_minimal_component_install(self):
        old_link = (self.workspace / "vs2019" / "2019" / "VC" / "Tools" / "MSVC"
                    / "14.29.0" / "bin" / "Hostx64" / "x64" / "link.exe")
        old_link.parent.mkdir(parents=True)
        old_link.write_bytes(b"old-link")
        link, rc = self._pair("new")
        run_calls = []
        run = make_run(calls=run_calls, versions=dict(GOOD_VERSIONS))
        resolve_calls = []

        def fake_resolve(_run, _log, _msvc=None, _rc=None):
            resolve_calls.append((_msvc, _rc))
            return None if len(resolve_calls) == 1 else (link, rc)

        with mock.patch.object(deps, "_resolve_msvc", side_effect=fake_resolve), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            result = deps.ensure_tools(
                self.workspace, {key: None for key in deps.TOOL_KEYS},
                download=make_download(), run=run,
                log=lambda _m: None, progress=lambda *a: None,
                cancelled=lambda: False)
        elevated = [cmd for cmd, _cwd, _codes, cancel in run_calls
                    if "start-process" in " ".join(map(str, cmd)).lower()]
        self.assertEqual(len(elevated), 1)
        script = elevated[0][-1]
        self.assertIn("-Verb RunAs", script)
        self.assertIn("-Wait", script)
        self.assertIn("-PassThru", script)
        self.assertIn(deps.VS_REQUIRED_WORKLOAD, script)
        self.assertIn("Microsoft.VisualStudio.Component.VC.Tools.x86.x64", script)
        self.assertIn("Microsoft.VisualStudio.Component.Windows11SDK.26100", script)
        self.assertIn("--nocache", script)
        self.assertIn("--norestart", script)
        self.assertNotIn("includeRecommended", script)
        self.assertIn("vs_BuildTools.exe", script)
        self.assertEqual(result["msvc"], link)
        self.assertEqual(result["rc"], rc)

    def test_failed_installer_stops_subsequent_work(self):
        run = make_run(versions=dict(GOOD_VERSIONS),
                       installer_error=RuntimeError("exit 1: disk full"))
        with mock.patch.object(deps, "_resolve_msvc", return_value=None), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            with self.assertRaisesRegex(RuntimeError, "disk full|install failed"):
                deps.ensure_tools(
                    self.workspace, {key: None for key in deps.TOOL_KEYS},
                    download=make_download(), run=run,
                    log=lambda _m: None, progress=lambda *a: None,
                    cancelled=lambda: False)

    def test_success_without_redetection_is_rejected(self):
        run = make_run(versions=dict(GOOD_VERSIONS), installer_output="ok")
        with mock.patch.object(deps, "_resolve_msvc", return_value=None), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            with self.assertRaisesRegex(RuntimeError, "no usable.*detected|still"):
                deps.ensure_tools(
                    self.workspace, {key: None for key in deps.TOOL_KEYS},
                    download=make_download(), run=run,
                    log=lambda _m: None, progress=lambda *a: None,
                    cancelled=lambda: False)

    def test_declined_uac_surfaces_approval_error(self):
        run = make_run(versions=dict(GOOD_VERSIONS),
                       installer_error=RuntimeError(
                           "exit 1: The operation was canceled by the user"))
        with mock.patch.object(deps, "_resolve_msvc", return_value=None), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            with self.assertRaisesRegex(RuntimeError, "UAC|declined|approval"):
                deps.ensure_tools(
                    self.workspace, {key: None for key in deps.TOOL_KEYS},
                    download=make_download(), run=run,
                    log=lambda _m: None, progress=lambda *a: None,
                    cancelled=lambda: False)


class AuthenticodeTest(BaseCase):
    def _run_with(self, **kwargs):
        calls = []
        run = make_run(calls=calls, **kwargs)
        dest = Path(self.tmp.name) / "signed.exe"
        dest.write_bytes(b"fake")
        return run, calls, dest

    def test_notvalid_status_is_refused(self):
        run, _calls, dest = self._run_with(sig_status="NotValid")
        with self.assertRaisesRegex(RuntimeError, "not Valid"):
            deps._verify_authenticode(run, dest, "VS Build Tools")

    def test_non_microsoft_signer_is_refused(self):
        run, _calls, dest = self._run_with(sig_subject="CN=Example, O=Example Inc")
        with self.assertRaisesRegex(RuntimeError, "not Microsoft"):
            deps._verify_authenticode(run, dest, "VS Build Tools")

    def test_unparseable_output_is_refused(self):
        def run(command, cwd=None, allowed_exit_codes=(0,), cancellable=True):
            return "not json at all"

        dest = Path(self.tmp.name) / "signed.exe"
        dest.write_bytes(b"fake")
        with self.assertRaisesRegex(RuntimeError, "unparseable|Refusing"):
            deps._verify_authenticode(run, dest, "VS Build Tools")

    def test_valid_microsoft_signature_passes_via_system_powershell(self):
        run, calls, dest = self._run_with()
        self.assertTrue(deps._verify_authenticode(run, dest, "VS Build Tools"))
        probe = [cmd for cmd, _cwd, _codes, _cancel in calls]
        self.assertEqual(len(probe), 1)
        self.assertEqual(probe[0][0], deps._powershell_exe())
        exe = probe[0][0].replace("\\", "/").lower()
        self.assertTrue(exe.endswith("powershell.exe") and "/" in exe)
        self.assertIn("Get-AuthenticodeSignature", probe[0][-1])


class RedistTest(BaseCase):
    def test_redist_installs_when_core_dlls_missing(self):
        link, rc = self._pair()
        run_calls = []
        run = make_run(calls=run_calls, versions=dict(GOOD_VERSIONS))
        with mock.patch.object(deps, "_resolve_msvc", return_value=(link, rc)), \
             mock.patch.object(deps, "_missing_core_runtimes",
                               side_effect=[["msvcp140_atomic_wait.dll"], []]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            result = deps.ensure_tools(
                self.workspace,
                {"git": None, "cmake": None, "ninja": None,
                 "clang": None, "clangxx": None,
                 "msvc": str(link), "rc": str(rc)},
                download=make_download(), run=run,
                log=lambda _m: None, progress=lambda *a: None,
                cancelled=lambda: False)
        elevated = [cmd for cmd, _cwd, _codes, _cancel in run_calls
                    if "start-process" in " ".join(map(str, cmd)).lower()]
        self.assertEqual(len(elevated), 1)
        script = elevated[0][-1]
        self.assertIn("vc_redist.x64.exe", script)
        self.assertIn("/quiet", script)
        self.assertIn("/norestart", script)
        self.assertIn("-Verb RunAs", script)
        self.assertTrue(result["msvc"])

    def test_atomic_wait_is_required_not_optional(self):
        self.assertIn("msvcp140_atomic_wait.dll", deps.VC_RUNTIME_DLLS)
        self.assertEqual(len(deps.VC_RUNTIME_DLLS), 4)
        sentinel = object()
        real_is_file = Path.is_file

        def fake_is_file(self):
            if self.name == "msvcp140_atomic_wait.dll":
                return False
            if self.name in deps.VC_RUNTIME_DLLS:
                return True
            return real_is_file(self)

        with mock.patch.object(Path, "is_file", fake_is_file):
            self.assertEqual(deps._missing_core_runtimes(),
                             ["msvcp140_atomic_wait.dll"])

    def test_redist_failure_without_dll_recovery_is_rejected(self):
        link, rc = self._pair()
        run = make_run(versions=dict(GOOD_VERSIONS), installer_output="ok")
        with mock.patch.object(deps, "_resolve_msvc", return_value=(link, rc)), \
             mock.patch.object(deps, "_missing_core_runtimes",
                               return_value=["msvcp140.dll"]), \
             mock.patch.object(deps, "_verify_sha256", return_value="ok"):
            with self.assertRaisesRegex(RuntimeError, "still missing"):
                deps.ensure_tools(
                    self.workspace,
                    {"git": None, "cmake": None, "ninja": None,
                     "clang": None, "clangxx": None,
                     "msvc": str(link), "rc": str(rc)},
                    download=make_download(), run=run,
                    log=lambda _m: None, progress=lambda *a: None,
                    cancelled=lambda: False)


class ArchiveSafetyTest(BaseCase):
    def test_archive_member_case_is_preserved(self):
        member = "Compiler/Intel-DetermineCompiler.cmake"
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind):
                archive = Path(self.tmp.name) / ("case." + kind)
                target = self.workspace / kind
                writer = _write_zip if kind == "zip" else _write_tar_xz
                extractor = deps._extract_zip if kind == "zip" else deps._extract_tar_xz
                writer(archive, {member: b"compiler rules"})
                extractor(archive, target, "compiler", lambda _message: None, lambda: False)
                self.assertEqual([path.name for path in target.iterdir()], ["Compiler"])
                self.assertEqual([path.name for path in (target / "Compiler").iterdir()],
                                 ["Intel-DetermineCompiler.cmake"])

    def test_sha_mismatch_aborts_before_extraction(self):
        dest = Path(self.tmp.name) / "bad.zip"
        _write_zip(dest, {"ninja.exe": b"tampered"})
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            deps._verify_sha256(dest, "0" * 64, "Ninja")

    def test_zip_slip_is_rejected(self):
        evil = Path(self.tmp.name) / "evil.zip"
        target = self.workspace / "tools" / "ninja"
        _write_zip(evil, {"../escape.exe": b"x"})
        with self.assertRaisesRegex(RuntimeError, "[Uu]nsafe archive"):
            deps._extract_zip(evil, target, "Ninja", lambda _m: None, lambda: False)
        self.assertFalse((target / "escape.exe").exists())

    def test_tar_symlink_forward_reference_materializes_as_copy(self):
        archive = Path(self.tmp.name) / "llvm.tar.xz"
        payload = b"fake-clang-binary"
        with tarfile.open(archive, "w:xz") as tarred:
            alias = tarfile.TarInfo("llvmroot/bin/clang++.exe")
            alias.type = tarfile.SYMTYPE
            alias.linkname = "clang.exe"
            tarred.addfile(alias)
            info = tarfile.TarInfo("llvmroot/bin/clang.exe")
            info.size = len(payload)
            tarred.addfile(info, io.BytesIO(payload))
        target = self.workspace / "tools" / "llvm"
        deps._extract_tar_xz(archive, target, "LLVM/Clang", lambda _m: None, lambda: False)
        clangxx = target / "llvmroot" / "bin" / "clang++.exe"
        self.assertTrue(clangxx.is_file())
        self.assertFalse(clangxx.is_symlink())
        self.assertEqual(clangxx.read_bytes(), payload)

    def test_tar_absolute_link_rejected_before_touching_system(self):
        archive = Path(self.tmp.name) / "evil.tar.xz"
        with tarfile.open(archive, "w:xz") as tarred:
            payload = b"real-tool"
            info = tarfile.TarInfo("llvmroot/bin/clang.exe")
            info.size = len(payload)
            tarred.addfile(info, io.BytesIO(payload))
            evil = tarfile.TarInfo("llvmroot/bin/clang++.exe")
            evil.type = tarfile.SYMTYPE
            evil.linkname = "C:/Windows/System32/evil.exe"
            tarred.addfile(evil)
        target = self.workspace / "tools" / "llvm"
        with self.assertRaisesRegex(RuntimeError, "[Uu]nsafe archive link"):
            deps._extract_tar_xz(archive, target, "LLVM/Clang", lambda _m: None, lambda: False)
        self.assertFalse((target / "llvmroot" / "bin" / "clang++.exe").exists())

    def test_tar_missing_link_target_is_rejected_not_stubbed(self):
        archive = Path(self.tmp.name) / "dangling.tar.xz"
        with tarfile.open(archive, "w:xz") as tarred:
            dangling = tarfile.TarInfo("pkg/tool-alias.exe")
            dangling.type = tarfile.SYMTYPE
            dangling.linkname = "gone.exe"
            tarred.addfile(dangling)
        target = self.workspace / "tools" / "pkg"
        with self.assertRaisesRegex(RuntimeError, "target missing"):
            deps._extract_tar_xz(archive, target, "Pkg", lambda _m: None, lambda: False)
        self.assertFalse((target / "pkg" / "tool-alias.exe").exists())

    def test_tar_hardlink_alias_materializes_without_ntfs(self):
        archive = Path(self.tmp.name) / "hard.tar.xz"
        payload = b"hardlink-payload"
        with tarfile.open(archive, "w:xz") as tarred:
            info = tarfile.TarInfo("pkg/tool.exe")
            info.size = len(payload)
            tarred.addfile(info, io.BytesIO(payload))
            alias = tarfile.TarInfo("pkg/tool-alias.exe")
            alias.type = tarfile.LNKTYPE
            alias.linkname = "pkg/tool.exe"
            alias.size = 0
            tarred.addfile(alias)
        target = self.workspace / "tools" / "pkg"
        deps._extract_tar_xz(archive, target, "Pkg", lambda _m: None, lambda: False)
        aliased = target / "pkg" / "tool-alias.exe"
        self.assertTrue(aliased.is_file())
        self.assertFalse(aliased.is_symlink())
        self.assertEqual(aliased.read_bytes(), payload)

    def test_zip_symlink_chain_materializes_as_copies(self):
        archive = Path(self.tmp.name) / "link.zip"
        real = Path(self.tmp.name) / "real.bin"
        real.write_bytes(b"real-payload")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            zipped.write(real, "tool/real.exe")
            for name, target in (("tool/mid.exe", "real.exe"),
                                 ("tool/alias.exe", "mid.exe")):
                link_info = zipfile.ZipInfo(name)
                link_info.create_system = 3
                link_info.external_attr = (0o120777 & 0xFFFF) << 16
                zipped.writestr(link_info, target)
        target = self.workspace / "tools" / "ziplink"
        deps._extract_zip(archive, target, "LinkPkg", lambda _m: None, lambda: False)
        aliased = target / "tool" / "alias.exe"
        self.assertTrue(aliased.is_file())
        self.assertFalse(aliased.is_symlink())
        self.assertEqual(aliased.read_bytes(), b"real-payload")

    def test_zip_absolute_link_is_rejected(self):
        archive = Path(self.tmp.name) / "evilink.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            link_info = zipfile.ZipInfo("tool/alias.exe")
            link_info.create_system = 3
            link_info.external_attr = (0o120777 & 0xFFFF) << 16
            zipped.writestr(link_info, "/etc/passwd")
        target = self.workspace / "tools" / "ziplink"
        with self.assertRaisesRegex(RuntimeError, "[Uu]nsafe archive link"):
            deps._extract_zip(archive, target, "LinkPkg", lambda _m: None, lambda: False)
        self.assertFalse((target / "tool" / "alias.exe").exists())


class FailureAndCancelTest(BaseCase):
    def test_failed_download_surfaces_tool_context(self):
        run = make_run(versions=dict(GOOD_VERSIONS))
        with mock.patch.object(deps, "_resolve_msvc", return_value=None), \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "[Dd]ownload failed"):
                deps.ensure_tools(
                    self.workspace, {key: None for key in deps.TOOL_KEYS},
                    download=make_download(fail=RuntimeError("timed out")),
                    run=run, log=lambda _m: None, progress=lambda *a: None,
                    cancelled=lambda: False)

    def test_cancellation_raises_without_downloading(self):
        download_calls = []
        with mock.patch.object(deps, "_resolve_msvc") as resolve, \
             mock.patch.object(deps, "_missing_core_runtimes", return_value=[]):
            with self.assertRaises(deps.BootstrapCancelled):
                deps.ensure_tools(
                    self.workspace, {key: None for key in deps.TOOL_KEYS},
                    download=make_download(calls=download_calls),
                    run=make_run(versions=dict(GOOD_VERSIONS)),
                    log=lambda _m: None, progress=lambda *a: None,
                    cancelled=lambda: True)
        self.assertEqual(download_calls, [])
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
