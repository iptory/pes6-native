#!/usr/bin/env python3
"""PES6 Native setup and builder.

This program contains no PES6 code or assets. It takes an ISO supplied by the
user, extracts the game partition locally, stages the pinned ReXGlue SDK and
the public project source, then builds the native game on the user's machine.

Build results are reported honestly: every stage shows live progress, failures
raise with the failing command and last output, and the full paths of the
built game executable and launcher are shown on success. The freshly built
game is launched automatically; if that launch fails the build is still
reported as complete but the launch failure is reported separately with the
manual command to run.
"""

import base64
import hashlib
import filecmp
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from collections import deque
from pathlib import Path

import dependencies
from installation import BuildSession, WorkspaceCleanupError, copy_notices, write_launcher, write_runtime_config
from processes import ProcessTree
from progress import BuildProgress, NINJA_PROGRESS

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    _TK_AVAILABLE = True
except ImportError:  # Headless/test environments without Tk.
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    _TK_AVAILABLE = False

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None


VERSION = "1.0.3"
SOURCE_URL = f"https://github.com/iptory/pes6-native/archive/refs/tags/v{VERSION}.zip"
SDK_URL = "https://github.com/rexglue/rexglue-sdk.git"
SDK_COMMIT = "c94f5ebdcb3c9d1a460ca48e04f9758448f8d518"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

BUNDLED_SOURCE_NAMES = (
    "pes6-native-source.zip",
    f"pes6-native-{VERSION}.zip",
)

REQUIRED_BUILD_ARTIFACTS = ("pes6.exe", "rexruntime.dll", "rexgpu-xenos.dll")
SYSTEM_DLLS = (
    "msvcp140.dll",
    "msvcp140_atomic_wait.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
def licenses_dir():
    """Location of the embedded/shipped license notices, or None."""
    for base in (resource_dir(), Path(__file__).resolve().parent):
        try:
            candidate = base / "licenses"
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def launch_game(exe_path, game_path, launcher_path=None):
    """Launch the freshly built game without blocking the builder.

    Returns the launched executable path. Raises RuntimeError with a manual
    fallback command when the launch cannot be started. Never launches when
    ``exe_path`` is missing.
    """
    exe = Path(exe_path)
    if not exe.is_file():
        raise RuntimeError(
            f"Build finished but the game executable is missing: {exe}. "
            + (f"Run {launcher_path} manually once it exists."
               if launcher_path else "Rebuild, then launch it manually.")
        )
    try:
        subprocess.Popen([str(exe), f"--game_data_root={game_path}"], cwd=exe.parent)
    except OSError as error:
        fallback = str(launcher_path or exe)
        raise RuntimeError(
            "Build complete, but the game could not be launched automatically "
            f"({error}). Run {fallback} manually."
        ) from error
    return exe


def remove_installer_after_exit():
    """Delete only this frozen installer, without leaving a cleanup script."""
    if not getattr(sys, "frozen", False):
        return
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256()
    with executable.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    path = str(executable).replace("'", "''")
    script = (
        f"$p='{path}'; $expected='{digest.hexdigest()}'; "
        "for($i=0;$i -lt 60;$i++){"
        "try{"
        "if(!(Test-Path -LiteralPath $p -PathType Leaf -ErrorAction Stop)){exit 0};"
        "if((Get-FileHash -LiteralPath $p -Algorithm SHA256 -ErrorAction Stop).Hash -ne $expected){exit 1};"
        "Remove-Item -LiteralPath $p -Force -ErrorAction Stop;exit 0}"
        "catch{Start-Sleep -Milliseconds 500}};exit 1"
    )
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe")
    return subprocess.Popen(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def load_drop_helpers():
    """Import Explorer drop validators, also working from a frozen bundle."""
    try:
        from win32_drop import try_enable_drop, validate_dropped_paths
        return try_enable_drop, validate_dropped_paths
    except ImportError:
        pass
    for base in (resource_dir(), Path(__file__).resolve().parent):
        try:
            if str(base) not in sys.path:
                sys.path.insert(0, str(base))
            from win32_drop import try_enable_drop, validate_dropped_paths
            return try_enable_drop, validate_dropped_paths
        except ImportError:
            continue
    raise ImportError("win32_drop helper module is missing from this installation.")


class CancelledError(dependencies.BootstrapCancelled):
    """Raised when the user cancels a running build."""


def which(name):
    found = shutil.which(name)
    if found:
        return Path(found)
    for candidate in (
        Path("C:/Program Files/LLVM/bin") / name,
        Path("C:/Program Files/CMake/bin") / name,
        Path("C:/Program Files/Git/cmd") / name,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links" / name,
    ):
        if candidate.is_file():
            return candidate
    return None


def find_rc():
    return dependencies._best_rc()


def find_msvc():
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if vswhere.is_file():
        try:
            result = subprocess.run(
                [str(vswhere), "-products", "*", "-find",
                 "VC/Tools/MSVC/**/bin/Hostx64/x64/link.exe"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace", timeout=10,
                creationflags=CREATE_NO_WINDOW, check=False,
            )
            for line in result.stdout.splitlines():
                if Path(line).is_file():
                    return Path(line)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def prereqs():
    return {
        "git": which("git.exe") or which("git"),
        "cmake": which("cmake.exe") or which("cmake"),
        "ninja": which("ninja.exe") or which("ninja"),
        "clang": which("clang.exe") or which("clang"),
        "clangxx": which("clang++.exe") or which("clang++"),
        "msvc": find_msvc(),
        "rc": find_rc(),
    }


def format_prereq_report(found):
    """Return (report_text, missing_names) for the prerequisite table."""
    lines = []
    missing = []
    for name in ("git", "cmake", "ninja", "clang", "clangxx", "msvc", "rc"):
        path = found.get(name)
        if path:
            lines.append(f"OK  {name}: {path}")
        else:
            lines.append(f"MISSING  {name}")
            missing.append(name)
    if PILImage is not None:
        lines.append("OK  pillow (optional): title icon enabled")
    else:
        lines.append("NOTE  pillow (optional): not installed; title icon will be skipped")
    return ("\n".join(lines), missing)


def source_url_for(version):
    return f"https://github.com/iptory/pes6-native/archive/refs/tags/v{version}.zip"


def find_bundled_source(search_dirs=None):
    """Return the packaged source archive, preferring the exact bundle name.

    Frozen builds ship one PyInstaller data file next to the executable:
    ``pes6-native-source.zip`` is preferred so the bundle does not depend on
    the builder version string; ``pes6-native-<VERSION>.zip`` is accepted as
    a fallback. Returns None when no bundle is present.
    """
    if search_dirs is None:
        search_dirs = [resource_dir()]
    for directory in search_dirs:
        base = Path(directory)
        for name in BUNDLED_SOURCE_NAMES:
            candidate = base / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


def repo_source_root():
    """Return the checkout root when running from source, else None."""
    root = Path(__file__).resolve().parent.parent
    try:
        if (root / "project" / "pes6_manifest.toml").is_file():
            return root
    except OSError:
        return None
    return None


def detect_source_root(extract_dir):
    """Find the source tree holding project/ inside an extracted archive.

    Accepts the flat layout and one level of wrapper directory: ``pes6-native/``
    (packaged bundle) or ``pes6-native-<version>/`` (GitHub tag zips).
    Raises a descriptive error when missing or ambiguous.
    """
    extract_dir = Path(extract_dir)
    if (extract_dir / "project" / "pes6_manifest.toml").is_file():
        return extract_dir
    candidates = []
    try:
        children = sorted(p for p in extract_dir.iterdir() if p.is_dir())
    except OSError as error:
        raise RuntimeError(f"Cannot list extracted source in {extract_dir}: {error}.")
    for child in children:
        try:
            if (child / "project" / "pes6_manifest.toml").is_file():
                candidates.append(child)
        except OSError:
            continue
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        top = [p.name for p in children[:8]]
        raise RuntimeError(
            "The source archive did not contain project/pes6_manifest.toml "
            f"(top-level entries: {top or ['<empty>']}). "
            "Use the release tag archive or a git archive of this repository."
        )
    names = sorted(p.name for p in candidates)
    raise RuntimeError(
        f"The source archive is ambiguous (multiple project trees: {names}). "
        "Use a clean release tag archive."
    )


def merge_tree(src, dst):
    """Copy source files without overwriting different existing files."""
    src = Path(src)
    dst = Path(dst)
    if src.is_dir():
        if dst.exists() and not dst.is_dir():
            raise RuntimeError(
                f"Cannot stage {src.name}: {dst} exists and is not a directory. "
                "Rename or remove it, then retry."
            )
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            merge_tree(child, dst / child.name)
    elif src.is_file():
        if dst.is_dir():
            raise RuntimeError(
                f"Cannot stage {src.name}: {dst} is a directory. "
                "Rename or remove it, then retry."
            )
        if dst.exists():
            if filecmp.cmp(src, dst, shallow=False):
                return
            raise RuntimeError(f"Cannot overwrite existing file {dst}. Choose a different output folder.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    else:
        raise RuntimeError(f"Source entry is missing: {src}.")


def install_source_tree(src_root, out):
    """Stage project/ and patches/ from src_root into out, preserving extras."""
    src_root = Path(src_root)
    out = Path(out)
    manifest = src_root / "project" / "pes6_manifest.toml"
    if not manifest.is_file():
        raise RuntimeError(
            f"Source tree at {src_root} has no project/pes6_manifest.toml. "
            "The archive or checkout is incomplete."
        )
    for child in (src_root / "project").iterdir():
        if child.name not in ("generated", "out", "CMakeUserPresets.json", "__pycache__"):
            merge_tree(child, out / "project" / child.name)
    if not (src_root / "patches" / "rexglue-sdk.patch").is_file():
        raise RuntimeError(f"Required SDK patch is missing from {src_root / 'patches'}.")
    merge_tree(src_root / "patches", out / "patches")
    if not (out / "project" / "pes6_manifest.toml").is_file():
        raise RuntimeError(
            f"Project source is still missing after staging into {out}. "
            "Check disk space and permissions, then retry."
        )
    return out / "project"


def download_file(url, dest, on_progress=None, timeout=60, chunk_size=1 << 20):
    """Download url to dest with progress and clear stall/timeout errors.

    on_progress(downloaded_bytes, total_bytes_or_None) may abort the download.
    A Content-Length mismatch raises instead of leaving a truncated file.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": f"PES6Builder/{VERSION}"})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Download failed: {url} (HTTP {error.code} {error.reason}). "
            f"Check that release tag v{VERSION} exists and you are online."
        )
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise RuntimeError(
            f"Download failed: {url} ({reason}). "
            "Check your connection and proxy settings, then retry."
        )
    except (socket.timeout, TimeoutError) as error:
        raise RuntimeError(
            f"Download timed out connecting to {url} after {timeout}s ({error}). "
            "Check your connection and try again."
        )
    except OSError as error:
        raise RuntimeError(f"Download failed: {url} ({error}).")
    total = None
    try:
        raw_total = response.getheader("Content-Length")
        if raw_total:
            total = int(raw_total)
            if total < 0:
                total = None
    except (ValueError, TypeError):
        total = None
    downloaded = 0
    try:
        with response, open(dest, "wb") as output:
            while True:
                try:
                    chunk = response.read(chunk_size)
                except (socket.timeout, TimeoutError) as error:
                    raise RuntimeError(
                        f"Download stalled: {downloaded} bytes received from {url} "
                        f"with no progress (read timed out: {error}). "
                        "Check your connection and try again."
                    )
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(downloaded, total)
    except RuntimeError:
        raise
    except OSError as error:
        raise RuntimeError(
            f"Cannot write download to {dest}: {error}. "
            "Check disk space and permissions."
        )
    if total is not None and downloaded != total:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            f"Download incomplete: got {downloaded} of {total} bytes from {url}. "
            "Check your connection and try again."
        )
    return downloaded


def repair_libmspack_stubs(sdk):
    """Replace libmspack symlink stubs with target copies (frozen-safe).

    Git for Windows without symlink support writes the link target text into
    a regular file, which the compiler then rejects. This inline port of
    tools/fix_libmspack_symlinks.py copies each stub's target over the stub.
    Idempotent. Returns the number of stubs fixed.
    """
    stub_dir = Path(sdk) / "thirdparty" / "libmspack" / "cabextract" / "mspack"
    if not stub_dir.is_dir():
        return 0
    fixed = 0
    try:
        names = sorted(os.listdir(stub_dir))
    except OSError:
        return 0
    for name in names:
        path = stub_dir / name
        try:
            if not path.is_file() or path.stat().st_size > 200:
                continue
            text = path.read_bytes().strip()
        except OSError:
            continue
        if not text or b"\n" in text:
            continue
        try:
            target = os.path.normpath(os.path.join(str(stub_dir), text.decode("latin-1")))
        except (UnicodeDecodeError, ValueError):
            continue
        try:
            if os.path.isfile(target) and os.path.getsize(target) > 200:
                shutil.copyfile(target, str(path))
                fixed += 1
        except OSError:
            continue
    return fixed


def git_apply_safe(git_exe, sdk_dir, patch_file, log_fn=None):
    """Apply the SDK patch idempotently; second runs report 'already'.

    Returns "applied" or "already".
    Raises with git's output when the patch does not apply cleanly.
    """
    patch_file = Path(patch_file)
    sdk_dir = Path(sdk_dir)
    if not patch_file.is_file():
        raise RuntimeError(f"Required SDK patch is missing: {patch_file}.")

    def _run(args):
        try:
            completed = subprocess.run(
                [str(git_exe)] + args,
                cwd=str(sdk_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Cannot start '{git_exe}': not found. Re-check prerequisites.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"git {' '.join(args)} timed out in {sdk_dir}.")
        except OSError as error:
            raise RuntimeError(f"Cannot run git: {error}.")
        return completed

    reverse_check = _run(["apply", "--reverse", "--check", str(patch_file)])
    if reverse_check.returncode == 0:
        if log_fn is not None:
            log_fn("SDK patch already applied; skipping.")
        return "already"
    check = _run(["apply", "--check", str(patch_file)])
    if check.returncode != 0:
        output = (check.stdout or "").strip()[-2000:]
        raise RuntimeError(
            f"SDK patch does not apply cleanly in {sdk_dir}. "
            "Delete the rexglue-sdk folder to re-clone, then retry. "
            f"git output: {output}"
        )
    applied = _run(["apply", str(patch_file)])
    if applied.returncode != 0:
        output = (applied.stdout or "").strip()[-2000:]
        raise RuntimeError(
            f"Failed to apply SDK patch in {sdk_dir} (exit {applied.returncode}). "
            f"git output: {output}"
        )
    if log_fn is not None:
        log_fn("Applied SDK patch.")
    return "applied"




def same_path(first, second):
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(
            str(Path(second).resolve())
        )
    except Exception:
        return False


def sync_game_dir(src, dst, on_progress=None):
    """Copy game files without modifying the selected folder or other files."""
    src = Path(src)
    dst = Path(dst)
    if not (src / "default.xex").is_file():
        raise RuntimeError(
            f"The selected folder does not contain default.xex: {src}. "
            "Select the folder that contains default.xex."
        )
    if dst.resolve().is_relative_to(src.resolve()):
        raise RuntimeError("The build game folder cannot be inside the selected game folder.")
    files = []
    for root, _dirs, filenames in os.walk(src):
        for name in filenames:
            full = Path(root) / name
            try:
                files.append(full.relative_to(src))
            except ValueError:
                continue
    files.sort()
    dst.mkdir(parents=True, exist_ok=True)
    total = len(files)
    for index, rel in enumerate(files, 1):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or not filecmp.cmp(src / rel, target, shallow=False):
                raise RuntimeError(f"Game file already exists with different content: {target}. Choose a new output folder.")
        else:
            temp_path = None
            try:
                with open(src / rel, "rb") as source, tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=".pes6-copy-", delete=False
                ) as temporary:
                    temp_path = Path(temporary.name)
                    while chunk := source.read(8 << 20):
                        temporary.write(chunk)
                        if on_progress is not None:
                            on_progress(index - 1, total, str(rel))
                shutil.copystat(src / rel, temp_path)
                temp_path.rename(target)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
        if on_progress is not None:
            on_progress(index, total, str(rel))
    return total


def write_build_presets(sdk, project, tools):
    """Write local CMake user presets with explicit absolute tool paths."""
    sdk = Path(sdk)
    project = Path(project)
    for key in ("clang", "clangxx", "rc", "ninja"):
        if not tools.get(key):
            raise RuntimeError(
                f"Cannot write build presets: required tool '{key}' was not found."
            )
    clang = Path(tools["clang"])
    clangxx = Path(tools.get("clangxx") or clang.with_name("clang++.exe"))
    rc = Path(tools["rc"])
    if not sdk.is_dir():
        raise RuntimeError(f"SDK directory is missing: {sdk}.")
    if not project.is_dir():
        raise RuntimeError(f"Project directory is missing: {project}.")
    sdk_presets = {
        "version": 6,
        "configurePresets": [{
            "name": "win-amd64-local",
            "inherits": "win-amd64",
            "cacheVariables": {
                "CMAKE_C_COMPILER": clang.as_posix(),
                "CMAKE_CXX_COMPILER": clangxx.as_posix(),
                "CMAKE_RC_COMPILER": rc.as_posix(),
                "CMAKE_MAKE_PROGRAM": Path(tools["ninja"]).as_posix(),
            },
        }],
        "buildPresets": [{
            "name": "win-amd64-local-release",
            "configurePreset": "win-amd64-local",
            "configuration": "Release",
        }],
    }
    project_presets = {
        "version": 6,
        "configurePresets": [{
            "name": "win-amd64-release-local",
            "inherits": "win-amd64-release",
            "cacheVariables": {
                "CMAKE_CXX_COMPILER": clangxx.as_posix(),
                "CMAKE_RC_COMPILER": rc.as_posix(),
                "CMAKE_MAKE_PROGRAM": Path(tools["ninja"]).as_posix(),
                "CMAKE_PREFIX_PATH": (sdk / "out/install/win-amd64-local").as_posix(),
            },
        }],
        "buildPresets": [{
            "name": "win-amd64-release-local",
            "configurePreset": "win-amd64-release-local",
        }],
    }
    (sdk / "CMakeUserPresets.json").write_text(json.dumps(sdk_presets, indent=2), encoding="utf-8")
    (project / "CMakeUserPresets.json").write_text(
        json.dumps(project_presets, indent=2), encoding="utf-8"
    )


def stage_runtime_dlls(build_dir, pes6_dir, system32_dir, log_fn=None):
    """Copy built artifacts plus VC++ runtimes into the PES6 folder.

    The build outputs are required: a missing pes6.exe/dll raises instead of
    reporting a fake success. Missing System32 VC++ DLLs are reported and
    skipped (some revisions lack msvcp140_atomic_wait.dll) so the installer
    says what it did instead of failing opaquely.
    """
    build_dir = Path(build_dir)
    pes6_dir = Path(pes6_dir)
    system32_dir = Path(system32_dir)
    pes6_dir.mkdir(parents=True, exist_ok=True)
    missing = [n for n in REQUIRED_BUILD_ARTIFACTS if not (build_dir / n).is_file()]
    if missing:
        raise RuntimeError(
            f"Build did not produce expected files in {build_dir}: "
            + ", ".join(missing)
            + ". See the build log above for the compile error."
        )
    for name in REQUIRED_BUILD_ARTIFACTS:
        shutil.copy2(build_dir / name, pes6_dir / name)
    copied_system = []
    missing_system = []
    for name in SYSTEM_DLLS:
        source_dll = system32_dir / name
        if source_dll.is_file():
            shutil.copy2(source_dll, pes6_dir / name)
            copied_system.append(name)
        else:
            missing_system.append(name)
    if log_fn is not None:
        try:
            log_fn("Staged build outputs: " + ", ".join(REQUIRED_BUILD_ARTIFACTS))
            if copied_system:
                log_fn("Staged system runtimes: " + ", ".join(copied_system))
            if missing_system:
                log_fn(
                    "NOTE: system runtimes not found (install the VC++ "
                    "Redistributable if the game fails to start): "
                    + ", ".join(missing_system)
                )
        except Exception:
            pass
    return (list(REQUIRED_BUILD_ARTIFACTS), copied_system, missing_system)


if _TK_AVAILABLE:

    class Builder(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"PES6 Native Builder {VERSION}")
            self.geometry("640x440")
            self.minsize(600, 440)
            self.iso = tk.StringVar()
            self.output = tk.StringVar(value=str(Path.home() / "Documents" / "Games"))
            self.stage_text = tk.StringVar(value="Checking prerequisites...")
            self.detail_text = tk.StringVar(value="")
            self.progress_text = tk.StringVar(value="Select an ISO to begin")
            self._progress_model = BuildProgress()
            self._last_activity = time.monotonic()
            self._process_tree = None
            self._process_cancellable = True
            self._closing = False
            self.msg_queue = queue.Queue()
            self.worker = None
            self.process = None
            self._process_lock = threading.Lock()
            self.cancel_event = threading.Event()
            self._stage = "Starting..."
            self._stage_started = time.monotonic()
            self._last_line = ""
            self._build_ok = False
            self._drop_target = None
            self._build_ui()
            self.update_idletasks()
            width = max(640, self.winfo_reqwidth())
            height = max(440, self.winfo_reqheight())
            self.minsize(width, height)
            self.geometry(f"{width}x{height}")
            self.after(100, self._drain)
            self.after(500, self._tick)
            self.after(200, self.check_prereqs)
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        def _build_ui(self):
            self.configure(background="#edf0f5")
            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure("PES6.TFrame", background="#edf0f5")
            style.configure("PES6.TLabel", background="#edf0f5", foreground="#18263d")
            style.configure("PES6.TButton", padding=(10, 6), font=("Segoe UI", 9))
            style.configure("Select.PES6.TButton", background="#b92336", foreground="white",
                            font=("Segoe UI", 10, "bold"), padding=(16, 8))
            style.map("Select.PES6.TButton",
                      background=[("disabled", "#d1d5dd"), ("active", "#92192a")],
                      foreground=[("disabled", "#566174"), ("active", "white")])
            style.configure("PES6.Horizontal.TProgressbar", background="#b92336",
                            troughcolor="#dce2eb", borderwidth=0)
            header = tk.Frame(self, background="#16253f", padx=18, pady=14)
            header.pack(fill="x")
            tk.Label(header, text="PES6  /  NATIVE", font=("Segoe UI", 18, "bold"),
                     background="#16253f", foreground="white").pack(side="left")
            tk.Label(header, text=f"BUILDER {VERSION}", font=("Segoe UI", 9),
                     background="#16253f", foreground="#d7e2f4").pack(side="right")
            frame = ttk.Frame(self, padding=(18, 12), style="PES6.TFrame")
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Your disc. Your game. Ready for Windows.",
                      font=("Segoe UI", 11, "bold"), style="PES6.TLabel").pack(anchor="w")
            self.drop_hint = ttk.Label(
                frame, text="Drop your PAL ISO here or select it to start building.",
                wraplength=560, style="PES6.TLabel")
            self.drop_hint.pack(anchor="w", pady=(4, 10))
            iso_row = ttk.Frame(frame, style="PES6.TFrame")
            iso_row.pack(fill="x")
            ttk.Entry(iso_row, textvariable=self.iso).pack(
                side="left", fill="x", expand=True, padx=(0, 8))
            ttk.Button(iso_row, text="Select ISO", style="Select.PES6.TButton",
                       command=self.pick_iso).pack(side="left")
            ttk.Button(iso_row, text="Folder", style="PES6.TButton",
                       command=self.pick_folder).pack(side="left", padx=(6, 0))
            out_row = ttk.Frame(frame, style="PES6.TFrame")
            out_row.pack(fill="x", pady=(10, 8))
            ttk.Label(out_row, text="Save to", style="PES6.TLabel").pack(side="left", padx=(0, 8))
            ttk.Entry(out_row, textvariable=self.output).pack(side="left", fill="x", expand=True)
            ttk.Button(out_row, text="Browse", command=self.pick_output).pack(side="left", padx=(8, 0))
            tk.Label(frame, textvariable=self.stage_text, background="#edf0f5",
                     foreground="#18263d", font=("Segoe UI", 10, "bold"),
                     wraplength=560, height=2, anchor="w", justify="left").pack(fill="x", pady=(8, 3))
            ttk.Label(frame, textvariable=self.progress_text, style="PES6.TLabel",
                      wraplength=560).pack(anchor="w", pady=(0, 3))
            self.progress = ttk.Progressbar(
                frame, mode="determinate", maximum=100, style="PES6.Horizontal.TProgressbar")
            self.progress.pack(fill="x", pady=(0, 4))
            tk.Label(frame, textvariable=self.detail_text, wraplength=560, height=2,
                     anchor="w", justify="left", font=("Segoe UI", 9),
                     background="#edf0f5", foreground="#18263d").pack(fill="x")
            # Keep actions and prerequisites above the expandable log at every window size.
            actions = ttk.Frame(frame, style="PES6.TFrame")
            actions.pack(fill="x", pady=(10, 6))
            self.start_button = ttk.Button(actions, text="Build / retry", command=self.start_build_requested)
            self.start_button.pack(side="left")
            self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel, state="disabled")
            self.cancel_button.pack(side="left", padx=(6, 0))
            ttk.Button(actions, text="Open game folder", command=self.open_output).pack(side="left", padx=(6, 0))
            self.licenses_button = ttk.Button(actions, text="Licenses", command=self.open_licenses)
            self.licenses_button.pack(side="right")
            prereq_row = ttk.Frame(frame, style="PES6.TFrame")
            prereq_row.pack(fill="x", pady=(0, 5))
            self.prereq_summary = ttk.Label(prereq_row, text="Checking tools...",
                                            wraplength=460, style="PES6.TLabel")
            self.prereq_summary.pack(side="left", fill="x", expand=True)
            ttk.Button(prereq_row, text="Tool details", command=self.show_prereq_details).pack(side="right")
            self.log_visible = tk.BooleanVar(value=False)
            self.remove_installer = tk.BooleanVar(value=bool(getattr(sys, "frozen", False)))
            options = ttk.Frame(frame, style="PES6.TFrame")
            options.pack(fill="x")
            ttk.Checkbutton(options, text="Show build log", variable=self.log_visible,
                            command=self._toggle_log).pack(side="left")
            if getattr(sys, "frozen", False):
                ttk.Checkbutton(options, text="Delete this installer after success",
                                variable=self.remove_installer).pack(side="right")
            self.log_view = tk.Text(frame, height=9, wrap="word", state="disabled",
                                    background="white", foreground="#18263d",
                                    font=("Consolas", 9), relief="flat")
            ttk.Label(frame, text="Uses your own ISO. No game code or data is included.",
                      style="PES6.TLabel", font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 0))
            self._prereq_report = ""
            self._prereq_missing: list = []
            try:
                try_enable_drop, _validate = load_drop_helpers()
                self._drop_target = try_enable_drop(self, self.on_drop_files)
                if self._drop_target is None:
                    self.drop_hint.configure(
                        text="Drag and drop is unavailable on this system; use Browse ISO."
                    )
            except Exception:
                self._drop_target = None

        def _toggle_log(self):
            if self.log_visible.get():
                self.log_view.pack(fill="both", expand=True, pady=(8, 0))
                self.geometry(f"{self.winfo_width()}x{self.winfo_height() + 180}")
            else:
                self.log_view.pack_forget()
                self.geometry(f"{self.winfo_width()}x{max(440, self.winfo_height() - 180)}")

        # -- Thread-safe reporting (callable from the worker thread). --
        def _post(self, message):
            self.msg_queue.put(message)

        def log(self, message):
            self._post(("log", str(message)))

        def report_stage(self, message, key=None):
            self._post(("stage", str(message), key))

        def report_progress(self, done, total, message=""):
            self._post(("progress", done, total, str(message)))

        def report_error(self, title, message):
            self._post(("error", str(title), str(message)))

        def report_finished(self, ok, message=""):
            self._post(("finished", bool(ok), str(message)))

        # -- Main-thread UI updates only. --
        def _append_log(self, message):
            self.log_view.configure(state="normal")
            self.log_view.insert("end", message + "\n")
            if int(self.log_view.index("end-1c").split(".")[0]) > 5000:
                self.log_view.delete("1.0", "1001.0")
            self.log_view.see("end")
            self.log_view.configure(state="disabled")

        def _render_progress(self):
            self.progress.configure(mode="determinate", maximum=100,
                                    value=self._progress_model.value)
            self.progress_text.set(self._progress_model.label())

        def _drain(self):
            for _ in range(200):
                try:
                    message = self.msg_queue.get_nowait()
                except queue.Empty:
                    break
                kind = message[0] if message else ""
                self._last_activity = time.monotonic()
                if kind == "log":
                    self._append_log(message[1])
                    tail = message[1][-200:]
                    if tail.strip():
                        self._last_line = tail
                        self.detail_text.set(tail)
                elif kind == "tools":
                    self._refresh_prereq_status(message[1])
                elif kind == "stage":
                    self._stage = message[1]
                    self._stage_started = time.monotonic()
                    self.stage_text.set(message[1])
                    self._last_line = ""
                    self.detail_text.set("")
                    key = message[2] if len(message) > 2 else None
                    if key:
                        self._progress_model.begin(key)
                    else:
                        self._progress_model.update(None, None)
                    self._render_progress()
                elif kind == "progress":
                    _k, done, total, text = message
                    if text:
                        self.detail_text.set(text)
                        self._last_line = text
                    self._progress_model.update(done, total)
                    self._render_progress()
                elif kind == "error":
                    _k, title, text = message
                    self._append_log(f"ERROR: {title}: {text}")
                    if not self.log_visible.get():
                        self.log_visible.set(True)
                        self._toggle_log()
                    try:
                        messagebox.showerror(title, text)
                    except Exception:
                        pass
                elif kind == "finished":
                    _k, ok, text = message
                    self._build_ok = ok
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    try:
                        self.progress.stop()
                    except Exception:
                        pass
                    if ok:
                        self._progress_model.finish()
                        self._render_progress()
                        self.stage_text.set(self._stage)
                        try:
                            messagebox.showinfo("Installation complete", text)
                        except Exception:
                            pass
                        self._on_close()
                        return
            self.after(100, self._drain)

        def _tick(self):
            try:
                if self.worker is not None and self.worker.is_alive():
                    elapsed = int(time.monotonic() - self._stage_started)
                    minutes, seconds = divmod(elapsed, 60)
                    inactive = int(time.monotonic() - self._last_activity)
                    self.stage_text.set(
                        f"{self._stage} ({minutes}m {seconds:02d}s; last activity {inactive}s ago)")
                    if self._last_line and not self.detail_text.get():
                        self.detail_text.set(self._last_line[-220:])
            except Exception:
                pass
            self.after(500, self._tick)

        def set_status(self, message):
            # Main-thread only. Workers must use report_stage() instead.
            self._stage = str(message)
            self._stage_started = time.monotonic()
            self.stage_text.set(str(message))

        def _refresh_prereq_status(self, found=None):
            found = prereqs() if found is None else found
            report, missing = format_prereq_report(found)
            self._prereq_report = report
            self._prereq_missing = missing
            try:
                if missing:
                    self.prereq_summary.configure(
                        text=f"Missing tools: {', '.join(missing)}. Selecting an ISO will ask to install them."
                    )
                else:
                    self.prereq_summary.configure(text="Tools detected; versions are checked during setup.")
            except Exception:
                pass
            self._update_start_state()

        def _update_start_state(self):
            try:
                busy = self.worker is not None and self.worker.is_alive()
                # Never hide a required action: the button stays enabled so an
                # empty selection reports what to do instead of going dead.
                self.start_button.configure(state="disabled" if busy else "normal")
            except Exception:
                pass

        def check_prereqs(self):
            self._refresh_prereq_status()
            if self._prereq_missing:
                self.set_status("Ready. Missing build tools will be downloaded automatically.")
            else:
                self.set_status("Ready. Drop your ISO or press Select ISO.")

        def show_prereq_details(self):
            report = self._prereq_report or format_prereq_report(prereqs())[0]
            message = (
                report
                + "\n\nRequired missing tools are fetched automatically when you select an ISO. "
                "Portable build tools are removed after installation. Microsoft compiler/SDK "
                "components may require administrator approval and remain installed if shared."
                + f"\n\nSource: v{VERSION}; SDK: {SDK_COMMIT}."
            )
            try:
                messagebox.showinfo("Prerequisites", message)
            except Exception:
                self.log(message)

        def pick_iso(self):
            path = filedialog.askopenfilename(title="Select your PAL PES6 ISO", filetypes=[("Xbox 360 ISO", "*.iso"), ("All files", "*.*")])
            if path:
                self.set_source_and_build(path)

        def pick_folder(self):
            path = filedialog.askdirectory(title="Select the folder containing default.xex")
            if path:
                self.set_source_and_build(path)

        def on_drop_files(self, paths):
            try:
                _enable, validate_dropped_paths = load_drop_helpers()
            except ImportError:
                messagebox.showerror("Drag and drop unavailable", "Use Browse ISO on this system.")
                return
            candidate, error = validate_dropped_paths(paths or [])
            if error:
                messagebox.showerror("Unsupported drop", error)
                self.log(f"Ignored drop ({len(paths or [])} item(s)): {error}")
                return
            self.set_source_and_build(str(candidate))

        def set_source_and_build(self, path):
            self.iso.set(str(path))
            self._update_start_state()
            self.start()

        def pick_output(self):
            path = filedialog.askdirectory(title="Select a build output folder")
            if path:
                self.output.set(path)

        def open_output(self):
            root = Path(self.output.get()).expanduser()
            path = root / "PES6" if (root / "PES6").is_dir() else root
            if not path.is_dir():
                self.log(f"The output folder has not been created yet: {path}")
                return
            if hasattr(os, "startfile"):
                try:
                    os.startfile(path)  # type: ignore[attr-defined]
                except OSError as error:
                    self.log(f"Cannot open output folder {path}: {error}")
            else:
                self.log(f"Output folder: {path}")

        def open_licenses(self):
            folder = licenses_dir()
            if folder is None:
                message = (
                    "License notices folder not found. When running from source, "
                    "see LICENSE, THIRD_PARTY_NOTICES.md, and builder/NOTICES.md "
                    "in the repository root."
                )
                try:
                    messagebox.showinfo("Licenses", message)
                except Exception:
                    pass
                self.log(message)
                return
            self.log(f"Opening license notices: {folder}")
            try:
                if hasattr(os, "startfile"):
                    os.startfile(folder)  # type: ignore[attr-defined]
                else:
                    messagebox.showinfo("Licenses", f"License notices: {folder}")
            except OSError as error:
                messagebox.showerror("Licenses", f"Cannot open {folder}: {error}")

        def start_build_requested(self):
            self.start()

        def start(self):
            if self.worker and self.worker.is_alive():
                return
            source = self.iso.get().strip()
            if not source:
                messagebox.showwarning("Missing source", "Drop your ISO (or Browse ISO) to start the build.")
                return
            if not Path(source).exists():
                messagebox.showerror("Missing source", "The selected ISO or folder does not exist.")
                return
            out = Path(self.output.get()).expanduser().resolve()
            if os.path.lexists(out / "PES6"):
                messagebox.showerror(
                    "Installation already exists",
                    f"{out / 'PES6'} already exists and will not be overwritten. "
                    "Open the existing game or choose a different output folder.",
                )
                return
            if not messagebox.askyesno("Install PES6", dependencies.bootstrap_consent(prereqs())):
                return
            try:
                out.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                messagebox.showerror("Output folder", f"Cannot create {out}: {error}")
                return
            self.cancel_event.clear()
            self._progress_model = BuildProgress()
            self._last_activity = time.monotonic()
            self._build_ok = False
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            self.worker = threading.Thread(target=self.build_worker, args=(Path(source), out), daemon=True)
            self.worker.start()

        def cancel(self):
            self.cancel_event.set()
            with self._process_lock:
                tree = self._process_tree
                cancellable = self._process_cancellable
            if tree is not None and cancellable:
                tree.terminate()
            if tree is not None and not cancellable:
                self.report_stage("Finishing Windows component installation before cancelling...")
                self.log("Cancellation will continue after the shared Windows installer finishes safely.")
            else:
                self.log("Cancellation requested...")

        def _on_close(self):
            if self.worker and self.worker.is_alive():
                if not self._closing and not self._build_ok:
                    self._closing = True
                    self.cancel()
                self.after(100, self._on_close)
            else:
                if self._build_ok and self.remove_installer.get():
                    try:
                        remove_installer_after_exit()
                    except OSError as error:
                        messagebox.showwarning(
                            "Installer cleanup",
                            f"The game is installed, but this downloaded installer could not "
                            f"be removed automatically: {error}\nYou can delete it manually.",
                        )
                self.destroy()

        # -- Worker-thread build steps (never touch Tk widgets directly). --
        def run(self, command, cwd=None, allowed_exit_codes=(0,), cancellable=True):
            if self.cancel_event.is_set():
                raise CancelledError("cancelled by user")
            if not cancellable:
                self.report_stage("Installing shared Windows components; waiting for Windows setup...")
            self.log("> " + " ".join(str(part) for part in command))
            command = [str(part) for part in command]
            try:
                proc = subprocess.Popen(
                    command, cwd=str(cwd) if cwd else None,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace",
                    creationflags=(CREATE_NO_WINDOW | 4) if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
            except OSError as error:
                raise RuntimeError(f"Cannot start '{command[0]}': {error}") from error
            tree = None
            try:
                tree = ProcessTree(proc)
                with self._process_lock:
                    self.process = proc
                    self._process_tree = tree
                    self._process_cancellable = cancellable
                    if self.cancel_event.is_set() and cancellable:
                        tree.terminate()
                output = deque(maxlen=200)
                assert proc.stdout is not None
                for raw in proc.stdout:
                    if self.cancel_event.is_set() and cancellable:
                        tree.terminate()
                        raise CancelledError("cancelled by user")
                    line = raw.rstrip("\r\n")
                    if line.strip():
                        self.log(line)
                        output.append(line)
                        count = NINJA_PROGRESS.match(line)
                        if count:
                            self.report_progress(int(count[1]), int(count[2]), line)
                code = proc.wait()
            finally:
                with self._process_lock:
                    self.process = None
                    self._process_tree = None
                    self._process_cancellable = True
                if tree is not None:
                    tree.close()
                elif proc.poll() is None:
                    proc.kill()
                proc.wait()
                if proc.stdout is not None:
                    proc.stdout.close()
            if self.cancel_event.is_set():
                raise CancelledError("cancelled by user")
            if code not in allowed_exit_codes:
                hint = "\n" + "\n".join(list(output)[-8:]) if output else ""
                raise RuntimeError(f"Command failed with exit code {code}: {command[0]}{hint}")
            if code == 3010:
                self.log("The Microsoft installer recommends a reboot; checking whether the tools are usable now.")
            return "\n".join(output)

        def download(self, url, destination):
            self.log(f"Downloading {url}")
            destination = Path(destination)
            tmp = destination.parent / (destination.name + ".part")
            last_post = [0.0]

            def on_progress(done, total):
                if self.cancel_event.is_set():
                    raise CancelledError("cancelled by user")
                now = time.monotonic()
                if now - last_post[0] >= 0.25 or (total and done >= total):
                    last_post[0] = now
                    if total:
                        self.report_progress(
                            done, total,
                            f"Downloaded {done / 1e6:.1f} / {total / 1e6:.1f} MB",
                        )
                    else:
                        self.report_progress(-1, 0, f"Downloaded {done / 1e6:.1f} MB")

            try:
                download_file(url, tmp, on_progress=on_progress)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            try:
                if destination.exists():
                    destination.unlink(missing_ok=True)
                tmp.rename(destination)
            except OSError as error:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeError(f"Cannot save download to {destination}: {error}.")
            self.log(f"Downloaded {destination.name}")
            self.report_progress(None, None, "Download complete; continuing setup...")

        def _load_xdvdfs(self):
            try:
                from xdvdfs_extract import Image as XdvdfsImage, extract, find_partition
                return XdvdfsImage, find_partition, extract
            except ImportError:
                pass
            for base in (resource_dir(), Path(__file__).resolve().parent.parent / "tools"):
                try:
                    if str(base) not in sys.path:
                        sys.path.insert(0, str(base))
                    from xdvdfs_extract import Image as XdvdfsImage, extract, find_partition
                    return XdvdfsImage, find_partition, extract
                except ImportError:
                    continue
            raise RuntimeError(
                "Game extractor module (xdvdfs_extract) is missing from this installation. "
                "Reinstall the builder with its bundled extractor."
            )

        def _extract_with_progress(self, extract_fn, image, partition, game_out, manifest_path):
            last_post = [0.0]

            def on_file(done, total, _path):
                if self.cancel_event.is_set():
                    raise CancelledError("cancelled by user")
                now = time.monotonic()
                if now - last_post[0] >= 0.5 or done >= total:
                    last_post[0] = now
                    self.report_progress(done, total, f"Extracting game files {done}/{total}")

            return extract_fn(
                image, partition, str(game_out),
                manifest_path=str(manifest_path), on_progress=on_file,
            )

        def prepare_source(self, out):
            out = Path(out)
            if (out / "project" / "pes6_manifest.toml").is_file():
                self.log("Project source already present; skipping download.")
                return out / "project"
            bundled = find_bundled_source()
            repo = repo_source_root()
            if bundled is None and repo is not None and not same_path(repo, out):
                self.report_stage("Using local repository source...")
                self.log(f"Using repository source at {repo}.")
                return install_source_tree(repo, out)
            with tempfile.TemporaryDirectory(prefix=".pes6-source-", dir=out) as temp:
                temp = Path(temp)
                archive = bundled
                if archive is None:
                    self.report_stage("Downloading project source...")
                    archive = temp / "pes6-native-source.zip"
                    self.download(source_url_for(VERSION), archive)
                else:
                    self.report_stage("Installing bundled project source...")
                    self.log(f"Using bundled source archive {archive.name}.")
                extract_dir = temp / "source"
                extract_dir.mkdir()
                try:
                    with zipfile.ZipFile(archive) as zipped:
                        names = zipped.namelist()
                        for index, name in enumerate(names, 1):
                            if self.cancel_event.is_set():
                                raise CancelledError("cancelled by user")
                            zipped.extract(name, extract_dir)
                            if index % 100 == 0 or index == len(names):
                                self.report_progress(index, len(names), f"Unpacking source {index}/{len(names)}")
                except zipfile.BadZipFile as error:
                    raise RuntimeError(f"Source archive is corrupt: {archive.name} ({error}).") from error
                return install_source_tree(detect_source_root(extract_dir), out)

        def extract_game(self, source, out):
            source = Path(source)
            out = Path(out)
            game_out = out / "game"
            if source.is_dir():
                if not (source / "default.xex").is_file():
                    raise RuntimeError(
                        f"The selected folder does not contain default.xex: {source}. "
                        "Select the folder that contains default.xex."
                    )
                if same_path(source, game_out):
                    self.log(f"Using game folder {source}.")
                    return game_out
                self.report_stage("Staging game folder...")
                self.log(
                    f"Staging your selected folder into {game_out} "
                    "(codegen requires game data at ../game)."
                )
                last_post = [0.0]

                def on_copy(done, total, _rel):
                    if self.cancel_event.is_set():
                        raise CancelledError("cancelled by user")
                    now = time.monotonic()
                    if now - last_post[0] >= 0.5 or done >= total:
                        last_post[0] = now
                        self.report_progress(done, total, f"Staging game files {done}/{total}")

                sync_game_dir(source, game_out, on_progress=on_copy)
                if not (game_out / "default.xex").is_file():
                    raise RuntimeError(f"Game staging left {game_out} without default.xex.")
                return game_out
            if not source.is_file():
                raise RuntimeError(f"The selected ISO was not found: {source}.")
            if game_out.exists():
                raise RuntimeError(
                    f"{game_out} already exists. To reuse it, select that extracted game folder, "
                    "or choose a new build output folder."
                )
            self.report_stage("Scanning ISO for the game partition (large file, please wait)...")
            XdvdfsImage, find_partition, extract_fn = self._load_xdvdfs()
            image = XdvdfsImage(str(source))
            try:
                last_scan = [0.0]

                def on_scan(done, total):
                    if self.cancel_event.is_set():
                        raise CancelledError("cancelled by user")
                    now = time.monotonic()
                    if now - last_scan[0] >= 0.5:
                        last_scan[0] = now
                        if total:
                            self.report_progress(
                                done, total,
                                f"Scanning ISO {done / 1e6:.0f} / {total / 1e6:.0f} MB",
                            )
                        else:
                            self.report_progress(-1, 0, f"Scanning ISO {done / 1e6:.0f} MB...")

                try:
                    partition = find_partition(image, on_progress=on_scan)
                except CancelledError:
                    raise
                except RuntimeError as error:
                    raise RuntimeError(
                        f"Could not find the game partition in {source.name}: {error}. "
                        "Confirm this is an unmodified PAL Xbox 360 ISO."
                    )
                if self.cancel_event.is_set():
                    raise CancelledError("cancelled by user")
                self.report_stage("Extracting game files (several minutes, progress below)...")
                with tempfile.TemporaryDirectory(prefix=".pes6-game-", dir=out) as temp:
                    staged = Path(temp) / "game"
                    manifest = Path(temp) / "game_manifest.json"
                    self._extract_with_progress(extract_fn, image, partition, staged, manifest)
                    if not (staged / "default.xex").is_file():
                        raise RuntimeError("Extraction finished without default.xex; the ISO may be corrupt.")
                    staged.rename(game_out)
                    if not (out / "game_manifest.json").exists():
                        manifest.rename(out / "game_manifest.json")
            finally:
                image.close()
            if not (game_out / "default.xex").is_file():
                raise RuntimeError(
                    f"Extraction finished without default.xex in {game_out}. "
                    "The ISO may be trimmed or corrupt."
                )
            return game_out

        def extract_icon(self, workspace, game, destination):
            icon_out = Path(workspace) / "_icons"
            exe = Path(workspace) / "rexglue-sdk/out/install/win-amd64-local/bin/rexglue.exe"
            if not exe.is_file() or PILImage is None:
                self.log("Icon step skipped (missing SDK tool or Pillow).")
                return
            icon_out.mkdir(parents=True, exist_ok=True)
            try:
                self.run([
                    str(exe), "init", "--project-name", "icons", "--xex-path",
                    str(game / "default.xex"), "achievements",
                    str(game / "default.xex"), str(icon_out),
                ])
                title = icon_out / "icons/title.png"
                final_icon = Path(destination) / "title.ico"
                if title.is_file():
                    with PILImage.open(title) as image:
                        image.convert("RGBA").save(
                            final_icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
                    self.log(f"Wrote original title icon to {final_icon}")
            except CancelledError:
                raise
            except Exception as error:
                self.log(f"Icon extraction skipped: {error}")

        def build_worker(self, source, out):
            original_path = os.environ.get("PATH", "")
            final = None
            try:
                with BuildSession(out) as session:
                    session.check_destination_free()
                    workspace = session.workspace
                    self.report_stage("Preparing required build tools...", "tools")
                    tools = dependencies.ensure_tools(
                        workspace, prereqs(), download=self.download, run=self.run,
                        log=self.log, progress=self.report_progress,
                        cancelled=self.cancel_event.is_set,
                    )
                    self._post(("tools", tools))
                    tool_dirs = [str(Path(tools[key]).parent)
                                 for key in ("rc", "git", "cmake", "ninja", "clang", "clangxx")
                                 if tools.get(key)]
                    os.environ["PATH"] = os.pathsep.join(tool_dirs + [os.environ.get("PATH", "")])
                    self.report_stage("Preparing project source...", "source")
                    project = self.prepare_source(workspace)
                    patch = workspace / "patches/rexglue-sdk.patch"
                    if not patch.is_file():
                        raise RuntimeError(f"The bundled SDK patch is missing: {patch}")
                    self.report_stage("Importing your game data...", "extract")
                    game = self.extract_game(source, workspace)
                    if self.cancel_event.is_set():
                        raise CancelledError("cancelled by user")
                    sdk = workspace / "rexglue-sdk"
                    self.report_stage("Downloading ReXGlue SDK...", "sdk_download")
                    git = [str(tools["git"]), "-c", "core.longpaths=true",
                           "-c", "http.lowSpeedLimit=1", "-c", "http.lowSpeedTime=120"]
                    self.run(git + ["clone", "--progress", SDK_URL, str(sdk)])
                    self.report_stage("Selecting the required SDK version...")
                    self.run(git + ["checkout", SDK_COMMIT], cwd=sdk)
                    self.report_stage("Downloading SDK components...")
                    self.run(git + ["submodule", "update", "--init", "--recursive", "--progress"], cwd=sdk)
                    self.report_stage("Configuring the SDK...", "sdk_configure")
                    git_apply_safe(str(tools["git"]), sdk, patch, log_fn=self.log)
                    fixed = repair_libmspack_stubs(sdk)
                    self.log(f"Repaired {fixed} Windows SDK symlink placeholders.")
                    write_build_presets(sdk, project, tools)
                    self.run([tools["cmake"], "--preset", "win-amd64-local"], cwd=sdk)
                    self.report_stage("Building the native runtime...", "sdk_build")
                    self.run([tools["cmake"], "--build", "--preset", "win-amd64-local-release",
                              "--target", "install", "--parallel", "2"], cwd=sdk)
                    rexglue = sdk / "out/install/win-amd64-local/bin/rexglue.exe"
                    if not rexglue.is_file():
                        raise RuntimeError(f"The runtime build did not produce {rexglue}.")
                    self.report_stage("Generating native game code...", "codegen")
                    self.run([rexglue, "codegen", "pes6_manifest.toml", "--ignore-stamp"], cwd=project)
                    if not (project / "generated").is_dir():
                        raise RuntimeError("Code generation did not produce the native game source.")
                    self.report_stage("Configuring the game...", "game_configure")
                    self.run([tools["cmake"], "--preset", "win-amd64-release-local"], cwd=project)
                    self.report_stage("Compiling your game...", "game_build")
                    self.run([tools["cmake"], "--build", "--preset", "win-amd64-release-local",
                              "--parallel", "2"], cwd=project)
                    self.report_stage("Assembling the installation...", "assemble")
                    build = project / "out/build/win-amd64-release-local"
                    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                    _, _, missing = stage_runtime_dlls(
                        build, session.staging, system32, log_fn=self.log)
                    if missing:
                        raise RuntimeError("Microsoft runtime installation is incomplete: " + ", ".join(missing))
                    self.extract_icon(workspace, game, session.staging)
                    game.rename(session.staging / "game")
                    write_runtime_config(session.staging, session.destination / "game")
                    write_launcher(session.staging)
                    copy_notices(repo_source_root() or resource_dir(), session.staging)
                    if self.cancel_event.is_set():
                        raise CancelledError("cancelled by user")
                    final = session.publish()
                    self.report_stage("Removing temporary build files...", "cleanup")
                # The source, SDK, compilers and build products are gone before any game starts.
                exe_path = final / "pes6.exe"
                launcher_path = final / "Play PES6.cmd"
                message = (
                    f"Installed successfully.\n\nGame: {exe_path}\n"
                    f"Launcher: {launcher_path}\n\nTemporary build files were removed."
                )
                try:
                    launch_game(exe_path, final / "game", launcher_path)
                    message += "\nThe game was started."
                except RuntimeError as error:
                    self.log(str(error))
                    message += f"\n\nAutomatic launch failed: {error}"
                try:
                    os.startfile(final)
                except OSError as error:
                    self.log(f"Cannot open the game folder: {error}")
                    message += f"\n\nOpen this folder manually: {final}"
                self.report_stage("Installation complete.")
                self.report_finished(True, message)
            except Exception as error:
                if not isinstance(error, WorkspaceCleanupError) and (
                    isinstance(error, dependencies.BootstrapCancelled) or self.cancel_event.is_set()
                ):
                    self.log("Installation cancelled; temporary files were removed.")
                    self.report_stage("Installation cancelled.")
                    self.report_finished(False, "cancelled")
                else:
                    self.log(traceback.format_exc())
                    title = "Installation incomplete" if final else "Installation failed"
                    text = str(error)
                    if final:
                        text += f"\n\nThe installed game is at {final}."
                    self.report_stage(f"{title}. See the build log.")
                    self.report_error(title, text)
                    self.report_finished(False, text)
            finally:
                os.environ["PATH"] = original_path
                self._post(("tools", None))


    if __name__ == "__main__":
        Builder().mainloop()
