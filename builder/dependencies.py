"""Session-owned build-tool bootstrap for the PES6 native builder.

Fetches only missing or too-old obligatory tools: git (SDK clone and
patch), CMake >= 3.25, Ninja, Clang >= 18, VS2022 MSVC link.exe plus
Windows SDK rc.exe (highest install wins; VS2019 and older rejected),
(msvcp140/msvcp140_atomic_wait/vcruntime140/vcruntime140_1). Portable tools
extract under <workspace>/tools and die with the disposable workspace. The only
system-wide installs are the signed Microsoft VS Build Tools bootstrapper
and VC++ Redistributable, run after one up-front parent consent; they are
never uninstalled here. No Python bootstrap: the SDK has no
find_package(Python) (codegen is the compiled rexglue.exe).

Pinned portable archives (SHA-256 verified before extraction):
- MinGit 2.56.0.4 x64: e3bf7835...ffa877
- CMake 4.4.5 windows-x86_64.zip: 326c8264...eff004
- Ninja 1.13.2 ninja-win.zip: 07fc8261...460b78
- LLVM/Clang 23.1.4 x86_64-pc-windows-msvc.tar.xz (~859 MB):
  854bffcf...3e8e4c6d2
Microsoft installers are evergreen aka.ms links (no pinned hash); each
must pass Authenticode (Status exactly "Valid", signer O=Microsoft
Corporation, via the absolute system powershell.exe) before running, then
installs elevated via Start-Process -Verb RunAs -Wait (CreateProcess
cannot UAC-elevate; error 740). --norestart is mandatory; 3010 is a
documented success code. Cancelling during a shared-component install
lets it finish/roll back safely first; cancellation applies right after.

VS components (learn.microsoft.com workload-component-id-vs-build-tools):
Workload.VCTools plus explicit Component.VC.Tools.x86.x64 (MSVC v143) and
Component.Windows11SDK.26100 (rc.exe), with --nocache; no
--includeRecommended (pulls portable-redundant bits like CMake).

Interface: ensure_tools(workspace, found, *, download, run, log,
progress, cancelled) returns the 7 TOOL_KEYS paths. run(command, cwd=None,
allowed_exit_codes=(0,), cancellable=True) returns output text; the two
Microsoft installers are called with (0, 3010) and cancellable=False.
cancelled() polls; raises BootstrapCancelled (the parent CancelledError
subclasses it). Install success is never trusted without re-detection;
PATH is prepended process-only.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import time
import zipfile
from pathlib import Path


class BootstrapCancelled(RuntimeError):
    """Raised when the user cancels the tool bootstrap; parent maps it."""


TOOL_KEYS = ("git", "cmake", "ninja", "clang", "clangxx", "msvc", "rc")

MIN_CMAKE = (3, 25, 0)
MIN_CLANG = (18, 0, 0)
MIN_NINJA = (1, 11, 0)
MIN_GIT = (2, 0, 0)
MIN_VS_MAJOR = 17
MIN_MSVC_TOOLSET_MINOR = 30  # 14.30+ ships with VS2022.

PINNED_ASSETS = {
    "git": {
        "version": "2.56.0.4",
        "url": "https://github.com/git-for-windows/git/releases/download/"
               "v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip",
        "sha256": "e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877",
        "archive": "MinGit-2.56.0.4-64-bit.zip",
        "size_mb": 38,
    },
    "cmake": {
        "version": "4.3.1",
        "url": "https://github.com/Kitware/CMake/releases/download/v4.3.1/"
               "cmake-4.3.1-windows-x86_64.zip",
        "sha256": "03a610be931546474e46a442d171ef10532f4fc847de9cf76b7a8065bbef6a23",
        "archive": "cmake-4.3.1-windows-x86_64.zip",
        "size_mb": 51,
    },
    "ninja": {
        "version": "1.13.2",
        "url": "https://github.com/ninja-build/ninja/releases/download/"
               "v1.13.2/ninja-win.zip",
        "sha256": "07fc8261b42b20e71d1720b39068c2e14ffcee6396b76fb7a795fb460b78dc65",
        "archive": "ninja-win.zip",
        "size_mb": 1,
    },
    "llvm": {
        "version": "23.1.4",
        "url": "https://github.com/llvm/llvm-project/releases/download/"
               "llvmorg-23.1.4/clang%2Bllvm-23.1.4-x86_64-pc-windows-msvc.tar.xz",
        "sha256": "854bffcfc84528e0ee92490ff5c178920abd358a79c3e98f053e41e3e8e4c6d2",
        "archive": "clang+llvm-23.1.4-x86_64-pc-windows-msvc.tar.xz",
        "size_mb": 859,
    },
}

VS_BOOTSTRAPPER_URL = "https://aka.ms/vs/17/release/vs_BuildTools.exe"
VS_REQUIRED_WORKLOAD = "Microsoft.VisualStudio.Workload.VCTools"
VS_BOOTSTRAP_ARGS = (
    "--quiet", "--wait", "--norestart", "--nocache",
    "--add", VS_REQUIRED_WORKLOAD,
    "--add", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
    "--add", "Microsoft.VisualStudio.Component.Windows11SDK.26100",
)

VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_REDIST_ARGS = ("/install", "/quiet", "/norestart")

VC_RUNTIME_DLLS = ("msvcp140.dll", "msvcp140_atomic_wait.dll",
                    "vcruntime140.dll", "vcruntime140_1.dll")

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _log_to(log, message):
    if log is not None:
        log(str(message))


def _progress_to(progress, done, total, message=""):
    if progress is not None:
        progress(int(done), int(total), str(message))


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise BootstrapCancelled("Tool bootstrap cancelled by user.")



def _parse_version(text):
    if not text:
        return None
    match = _VERSION_RE.search(str(text))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _probe(run, exe, args):
    """Return the tool version tuple, or None when unusable."""
    try:
        output = run([str(exe)] + list(args))
    except BootstrapCancelled:
        raise
    except Exception:
        return None
    return _parse_version(output)


def _tool_ok(run, path, minimum):
    if path is None:
        return False
    version = _probe(run, path, ["--version"])
    return version is not None and version >= minimum


def _as_path(value):
    if not value:
        return None
    try:
        path = Path(value)
    except Exception:
        return None
    return path if path.is_file() else None


def _vswhere_exe():
    base = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidate = Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    return candidate if candidate.is_file() else None


def _toolset_ok(link_path):
    """True only for a VS2022-or-newer MSVC linker path."""
    text = str(link_path)
    lowered = text.lower()
    if "\\2019\\" in lowered or "\\2017\\" in lowered or "/2019/" in lowered:
        return False
    match = re.search(r"MSVC[/\\]14\.(\d+)", text)
    if match:
        try:
            return int(match.group(1)) >= MIN_MSVC_TOOLSET_MINOR
        except ValueError:
            return False
    return "\\2022\\" in lowered or "/2022/" in lowered


def _link_candidates(installation_path):
    pattern = str(Path(installation_path) / "VC" / "Tools" / "MSVC" / "*"
                  / "bin" / "Hostx64" / "x64" / "link.exe")
    found = []
    for candidate in glob.glob(pattern):
        path = Path(candidate)
        if not path.is_file():
            continue
        match = re.search(r"MSVC[/\\]14\.(\d+)\.(\d+)", str(path))
        order = (0, 0)
        if match:
            order = (int(match.group(1)), int(match.group(2)))
        found.append((order, path))
    found.sort(key=lambda item: item[0])
    return [path for _order, path in found]


def _vswhere_best(run):
    """Highest VS2022+ MSVC link.exe via vswhere, else None."""
    vswhere = _vswhere_exe()
    if vswhere is None:
        return None
    try:
        output = run(
            [str(vswhere), "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-format", "json"])
    except BootstrapCancelled:
        raise
    except Exception:
        return None
    try:
        data = json.loads(str(output))
    except Exception:
        data = None
    if isinstance(data, list) and data:
        ranked = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            install_path = entry.get("installationPath")
            major = _parse_version(str(entry.get("installationVersion", "")))
            if not install_path or major is None or major[0] < MIN_VS_MAJOR:
                continue
            for link in _link_candidates(install_path):
                match = re.search(r"MSVC[/\\](14\.\d+\.\d+)", str(link))
                ranked.append((match.group(1) if match else "", link))
        if ranked:
            ranked.sort(key=lambda item: item[0])
            return ranked[-1][1]
    try:
        lines = run(
            [str(vswhere), "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"])
    except BootstrapCancelled:
        raise
    except Exception:
        return None
    best = None
    best_key = ""
    for line in str(lines).splitlines():
        line = line.strip()
        if not line or "2022" not in line:
            continue
        for link in _link_candidates(line):
            match = re.search(r"MSVC[/\\](14\.\d+\.\d+)", str(link))
            key = match.group(1) if match else str(link)
            if key >= best_key:
                best_key = key
                best = link
    return best


def _disk_best_msvc():
    """Highest VS2022 link.exe from default install roots (no vswhere)."""
    best = None
    best_key = ""
    for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
        for program_files in (r"C:\Program Files (x86)", r"C:\Program Files"):
            pattern = str(Path(program_files) / "Microsoft Visual Studio"
                           / "2022" / edition / "VC" / "Tools" / "MSVC" / "*"
                           / "bin" / "Hostx64" / "x64" / "link.exe")
            for candidate in glob.glob(pattern):
                path = Path(candidate)
                if not path.is_file():
                    continue
                match = re.search(r"MSVC[/\\](14\.\d+\.\d+)", str(path))
                key = match.group(1) if match else str(path)
                if key >= best_key:
                    best_key = key
                    best = path
    return best


def _best_rc():
    roots = []
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows Kits\Installed Roots",
                0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
            ) as key:
                roots.append(Path(winreg.QueryValueEx(key, "KitsRoot10")[0]))
        except OSError:
            pass
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]) / "Windows Kits" / "10")
    candidates = [Path(path) for root in roots
                  for path in glob.glob(str(root / "bin" / "*" / "x64" / "rc.exe"))]
    return max(candidates, key=lambda path: _parse_version(path.parent.parent.name) or (0, 0, 0),
               default=None)


def _validate_msvc_pair(msvc, rc):
    return (
        msvc is not None and msvc.is_file() and _toolset_ok(msvc)
        and rc is not None and rc.is_file()
    )


def _resolve_msvc(run, log, found_msvc=None, found_rc=None):
    """Highest usable (link.exe, rc.exe) pair, preferring newest install."""
    best_link = _vswhere_best(run) or _disk_best_msvc()
    best_rc = _best_rc()
    if best_link is not None and best_rc is not None and _toolset_ok(best_link):
        if found_msvc is not None and Path(best_link) != Path(found_msvc):
            _log_to(log, f"Using newest MSVC linker: {best_link} (was {found_msvc}).")
        return (Path(best_link), Path(best_rc))
    if _validate_msvc_pair(found_msvc, found_rc):
        return (Path(found_msvc), Path(found_rc))
    return None


def _system32_dir():
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def _missing_core_runtimes():
    system32 = _system32_dir()
    missing = []
    for name in VC_RUNTIME_DLLS:
        try:
            if not (system32 / name).is_file():
                missing.append(name)
        except OSError:
            missing.append(name)
    return missing


def _verify_sha256(path, expected, name="file", *, cancelled=None):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            _check_cancelled(cancelled)
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != str(expected).lower():
        raise RuntimeError(
            f"SHA-256 mismatch for {name}: expected {expected}, got {actual}. "
            "The downloaded file was rejected."
        )
    return actual


def _download_and_verify(*, name, url, sha256, dest, download, log, cancelled):
    _check_cancelled(cancelled)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _log_to(log, f"Downloading {name}... {url}")
    try:
        download(url, dest)
    except BootstrapCancelled:
        raise
    except Exception as exc:
        raise RuntimeError(f"Download failed for {name}: {exc}.") from exc
    _check_cancelled(cancelled)
    _log_to(log, f"Checking SHA-256 for {name}...")
    _verify_sha256(dest, sha256, name, cancelled=cancelled)
    _log_to(log, f"Verified SHA-256 for {name}.")
    return dest


def _fetch(*, url, dest, label, download):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        download(url, dest)
    except BootstrapCancelled:
        raise
    except Exception as exc:
        raise RuntimeError(f"Download failed for {label}: {exc}.") from exc
    return dest


def _is_abs_link_target(raw):
    if not raw:
        return True
    if raw.startswith("/") or raw.startswith("\\"):
        return True
    if os.path.isabs(raw):
        return True
    return bool(_DRIVE_ABS_RE.match(raw))


def _within_root(root, target):
    root, target = os.path.normcase(root), os.path.normcase(target)
    return target == root or target.startswith(root + os.sep)


def _dest_parts(dest_dir, arc_name):
    parts = [entry for entry in arc_name.split("/") if entry not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return os.path.normpath(os.path.join(str(dest_dir), *parts))


def _copy_link_source(*, source, target, name, arc):
    if not Path(source).is_file():
        raise RuntimeError(f"Archive link target missing in {name}: {arc!r}.")
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot materialize {name} entry {arc!r}: {exc}."
        ) from exc


def _resolve_link(*, arc, linkname, is_hard, table, name):
    if _is_abs_link_target(linkname):
        raise RuntimeError(f"Unsafe archive link in {name}: {arc!r} -> {linkname!r}.")
    if is_hard:
        resolved = posixpath.normpath(linkname)
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(arc), linkname))
    if resolved in ("", ".") or resolved.startswith("..") or resolved not in table:
        raise RuntimeError(f"Archive link target missing in {name}: {arc!r}.")
    return resolved


def _materialize_links(dest_dir, root, name, table, pending, is_hard_map, cancelled):
    """Copy in-archive link targets over link entries; no symlink privileges."""
    done = set()
    emitting = set()

    def emit(arc):
        _check_cancelled(cancelled)
        if arc in done:
            return
        resolved = _resolve_link(arc=arc, linkname=pending[arc],
                                 is_hard=is_hard_map[arc], table=table, name=name)
        if resolved in pending:
            if resolved in emitting:
                raise RuntimeError(f"Cyclic archive link in {name}: {arc!r}.")
            emitting.add(arc)
            emit(resolved)
            emitting.discard(arc)
        source = _dest_parts(dest_dir, resolved)
        target = _dest_parts(dest_dir, arc)
        if source is None or target is None or not _within_root(root, source):
            raise RuntimeError(f"Unsafe archive link in {name}: {arc!r}.")
        _copy_link_source(source=source, target=target, name=name, arc=arc)
        done.add(arc)

    for arc in pending:
        emit(arc)


def _copy_archive_member(source, out, label, log, cancelled):
    copied = 0
    last_update = time.monotonic()
    while True:
        _check_cancelled(cancelled)
        chunk = source.read(8 << 20)
        if not chunk:
            break
        out.write(chunk)
        copied += len(chunk)
        now = time.monotonic()
        if now - last_update >= 1:
            _log_to(log, f"Extracting {label}: {copied / 1e6:.1f} MB")
            last_update = now


def _extract_zip(archive, dest_dir, name, log, cancelled):
    """Privilege-free zip extraction; links materialize as copies."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = os.path.normcase(str(dest_dir.resolve()))
    with zipfile.ZipFile(archive) as zipped:
        infos = zipped.infolist()
        _log_to(log, f"Extracting {name} ({len(infos)} entries)...")
        table = {info.filename: info for info in infos}
        pending = {}
        is_hard = {}
        for index, info in enumerate(infos, 1):
            if index % 50 == 0:
                _check_cancelled(cancelled)
            if not info.filename or _is_abs_link_target(info.filename):
                raise RuntimeError(f"Unsafe archive entry in {name}: {info.filename!r}.")
            target = _dest_parts(dest_dir, info.filename)
            if target is None or not _within_root(root, target):
                raise RuntimeError(f"Unsafe archive entry in {name}: {info.filename!r}.")
            if info.is_dir():
                Path(target).mkdir(parents=True, exist_ok=True)
                continue
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                try:
                    pending[info.filename] = zipped.read(info.filename).decode(
                        "utf-8", "replace").strip()
                except Exception as exc:
                    raise RuntimeError(
                        f"Cannot extract {name} entry {info.filename!r}: {exc}."
                    ) from exc
                is_hard[info.filename] = False
                continue
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            try:
                with zipped.open(info) as source, open(target, "wb") as out:
                    _copy_archive_member(source, out, f"{name} / {info.filename}", log, cancelled)
            except BootstrapCancelled:
                raise
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot extract {name} entry {info.filename!r}: {exc}."
                ) from exc
        _materialize_links(dest_dir, root, name, table, pending, is_hard, cancelled)
    _check_cancelled(cancelled)
    return dest_dir


def _extract_tar_xz(archive, dest_dir, name, log, cancelled):
    """Extract in one pass, with cancellation during large members."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = os.path.normcase(str(dest_dir.resolve()))
    _log_to(log, f"Extracting {name}...")
    table, pending, is_hard = {}, {}, {}
    with tarfile.open(archive, "r|xz") as tarred:
        for index, member in enumerate(tarred, 1):
            _check_cancelled(cancelled)
            table[member.name] = member
            if index % 200 == 0:
                _log_to(log, f"Extracting {name}: {index} entries")
            if not member.name or _is_abs_link_target(member.name):
                raise RuntimeError(f"Unsafe archive entry in {name}: {member.name!r}.")
            target = _dest_parts(dest_dir, member.name)
            if target is None or not _within_root(root, target):
                raise RuntimeError(f"Unsafe archive entry in {name}: {member.name!r}.")
            if member.issym() or member.islnk():
                pending[member.name] = member.linkname
                is_hard[member.name] = member.islnk()
                continue
            if member.isdir():
                Path(target).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(f"Unsupported archive entry in {name}: {member.name!r}.")
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            source = tarred.extractfile(member)
            if source is None:
                raise RuntimeError(f"Cannot extract {name} entry {member.name!r}: no data.")
            with source, open(target, "wb") as out:
                _copy_archive_member(source, out, f"{name} / {member.name}", log, cancelled)
        _materialize_links(dest_dir, root, name, table, pending, is_hard, cancelled)
    _check_cancelled(cancelled)
    return dest_dir


def _locate(root, filename, extra_dirs=("bin", "cmd")):
    root = Path(root)
    for extra in ("",) + tuple(extra_dirs):
        candidate = root / extra / filename if extra else root / filename
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        children = []
    for child in children:
        for extra in extra_dirs:
            candidate = child / extra / filename
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    try:
        hits = sorted(p for p in root.rglob(filename) if p.is_file())
    except OSError:
        hits = []
    return hits[0] if hits else None


def _prepend_process_path(dirs, log):
    clean = [str(d) for d in dirs if d]
    if not clean:
        return
    os.environ["PATH"] = os.pathsep.join(clean + [os.environ.get("PATH", "")])
    _log_to(log, "PATH updated for this process only: " + os.pathsep.join(clean))


_PORTABLE_SPECS = {
    "git": {"asset": "git", "display": "MinGit", "subdir": "git",
            "kind": "zip", "wanted": ("git.exe",), "minimum": None},
    "cmake": {"asset": "cmake", "display": "CMake", "subdir": "cmake",
              "kind": "zip", "wanted": ("cmake.exe",), "minimum": MIN_CMAKE},
    "ninja": {"asset": "ninja", "display": "Ninja", "subdir": "ninja",
              "kind": "zip", "wanted": ("ninja.exe",), "minimum": MIN_NINJA,
              "extra_dirs": ()},
    "llvm": {"asset": "llvm", "display": "LLVM/Clang", "subdir": "llvm",
             "kind": "tar", "wanted": ("clang.exe", "clang++.exe"),
             "minimum": MIN_CLANG},
}


def _install_portable(action, *, tools_dir, downloads_dir,
                       download, run, log, cancelled):
    """Download, verify, extract, and version-check one portable tool."""
    spec = _PORTABLE_SPECS[action]
    asset = PINNED_ASSETS[spec["asset"]]
    label = f"{spec['display']} {asset['version']}"
    dest = Path(downloads_dir) / asset["archive"]
    _download_and_verify(name=label, url=asset["url"], sha256=asset["sha256"],
                         dest=dest, download=download, log=log, cancelled=cancelled)
    target = Path(tools_dir) / spec["subdir"]
    extractor = _extract_tar_xz if spec["kind"] == "tar" else _extract_zip
    extractor(dest, target, label, log, cancelled)
    found = []
    for wanted in spec["wanted"]:
        exe = _locate(target, wanted, extra_dirs=spec.get("extra_dirs", ("bin", "cmd")))
        if exe is None:
            raise RuntimeError(
                f"{label} extraction finished without {wanted} in {target}. "
                "The incomplete tool cannot be used."
            )
        found.append(exe)
    versions = [_probe(run, exe, ["--version"]) for exe in found]
    minimum = spec["minimum"]
    if any(version is None for version in versions) or (
            minimum is not None and any(version < minimum for version in versions)):
        raise RuntimeError(
            f"Downloaded {label} is not usable at {found[0]} "
            f"(got {versions[0]})."
        )
    _prepend_process_path([found[0].parent], log)
    _log_to(log, f"Portable {label} ready: {found[0]}.")
    return found[0] if len(found) == 1 else tuple(found)


def _powershell_exe():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _verify_authenticode(run, path, name):
    """Require Authenticode Status exactly Valid, signer Microsoft."""
    script = (
        "$sig = Get-AuthenticodeSignature -LiteralPath '"
        + str(path).replace("'", "''") + "'; "
        "$sig | Select-Object "
        "@{Name='Status';Expression={$_.Status.ToString()}},"
        "@{Name='Subject';Expression={"
        "if ($_.SignerCertificate) { $_.SignerCertificate.Subject } else { '' }}} "
        "| ConvertTo-Json -Compress"
    )
    try:
        output = run([_powershell_exe(), "-NoProfile", "-NonInteractive",
                      "-Command", script])
    except BootstrapCancelled:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Signature check could not run for {name}: {exc}."
        ) from exc
    try:
        data = json.loads(str(output).strip())
    except Exception:
        raise RuntimeError(
            f"Signature check failed for {name}: unparseable output. Refusing to run it."
        )
    status = data.get("Status") if isinstance(data, dict) else None
    subject = (data.get("Subject") or "") if isinstance(data, dict) else ""
    if status != "Valid":
        raise RuntimeError(
            f"Signature check failed for {name}: status {status!r} "
            "is not Valid. Refusing to run it."
        )
    if not re.search(r"(?:^|,\s*)O=Microsoft Corporation(?:,|$)", subject):
        raise RuntimeError(
            f"Signature check failed for {name}: signer {subject!r} "
            "is not Microsoft Corporation. Refusing to run it."
        )
    return True


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _elevated_install(exe, args, *, run, log, label):
    """Run a verified Microsoft installer elevated; never cancellable mid-flight.

    CreateProcess cannot UAC-elevate (error 740), so the installer runs via
    Start-Process -Verb RunAs -Wait -PassThru, preserving its real exit
    code (0 ok, 3010 reboot-note). cancellable=False: a shared-component
    install finishes/rolls back safely before cancellation applies.
    """
    arglist = ",".join(_ps_quote(item) for item in args)
    script = (
        "$ErrorActionPreference='Stop'; $p = Start-Process -FilePath " + _ps_quote(exe)
        + " -ArgumentList " + arglist
        + " -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    _log_to(log, f"Requesting Windows administrator permission (UAC) for {label}...")
    _log_to(log, "If you cancel now, a running shared install finishes safely "
                 "first; cancellation applies right after.")
    command = [_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script]
    try:
        output = run(command, allowed_exit_codes=(0, 3010), cancellable=False)
    except BootstrapCancelled:
        raise
    except Exception as exc:
        text = str(exc)
        lowered = text.lower()
        if ("canceled" in lowered or "cancelled" in lowered
                or "1223" in text or "0x800704c7" in lowered
                or "uac" in lowered):
            raise RuntimeError(
                f"{label} elevation was declined (UAC prompt needs approval): {text}."
            ) from exc
        raise RuntimeError(f"{label} install failed: {text}.") from exc
    if output:
        _log_to(log, f"{label} output (tail): " + str(output)[-2000:])
    return output


def _install_vs_build_tools(*, downloads_dir, download, run, log, cancelled):
    _check_cancelled(cancelled)
    dest = _fetch(url=VS_BOOTSTRAPPER_URL, dest=Path(downloads_dir) / "vs_BuildTools.exe",
                  label="VS Build Tools", download=download)
    _verify_authenticode(run, dest, "VS Build Tools")
    _check_cancelled(cancelled)
    return _elevated_install(dest, VS_BOOTSTRAP_ARGS, run=run, log=log,
                             label="VS Build Tools")


def _install_vc_redist(*, downloads_dir, download, run, log, cancelled):
    _check_cancelled(cancelled)
    dest = _fetch(url=VC_REDIST_URL, dest=Path(downloads_dir) / "vc_redist.x64.exe",
                  label="VC++ Redistributable", download=download)
    _verify_authenticode(run, dest, "VC++ Redistributable")
    _check_cancelled(cancelled)
    return _elevated_install(dest, VC_REDIST_ARGS, run=run, log=log,
                             label="VC++ Redistributable")


def ensure_tools(workspace, found, *, download, run, log, progress, cancelled):
    """Bootstrap missing or too-old build tools; return the completed dict."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    tools_dir = workspace / "tools"
    downloads_dir = workspace / "downloads"
    tools_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    _check_cancelled(cancelled)

    found = dict(found or {})
    result = {key: _as_path(found.get(key)) for key in TOOL_KEYS}

    _progress_to(progress, 0, 1, "Checking build tools...")
    need_git = not _tool_ok(run, result["git"], MIN_GIT)
    need_cmake = not _tool_ok(run, result["cmake"], MIN_CMAKE)
    need_ninja = not _tool_ok(run, result["ninja"], MIN_NINJA)
    need_llvm = not (_tool_ok(run, result["clang"], MIN_CLANG)
                     and _tool_ok(run, result["clangxx"], MIN_CLANG))
    _check_cancelled(cancelled)

    msvc_pair = _resolve_msvc(run, log, result["msvc"], result["rc"])
    if msvc_pair is None:
        _log_to(log, "MSVC linker or Windows SDK rc.exe is missing or too old.")
    else:
        result["msvc"], result["rc"] = msvc_pair
    need_vs = msvc_pair is None
    _check_cancelled(cancelled)

    need_redist = bool(_missing_core_runtimes())
    if need_redist:
        _log_to(log, "VC++ runtime DLLs missing; redistributable needed.")

    actions = []
    if need_git:
        actions.append("git")
    if need_cmake:
        actions.append("cmake")
    if need_ninja:
        actions.append("ninja")
    if need_llvm:
        actions.append("llvm")
    if need_vs:
        actions.append("vs")
    if need_redist:
        actions.append("redist")
    total = len(actions) + 1
    if not actions:
        _log_to(log, "All build tools already present and sufficiently new.")
        _progress_to(progress, total, total, "All build tools present.")
        return result

    for index, action in enumerate(actions, 1):
        _check_cancelled(cancelled)
        _progress_to(progress, index, total, f"Installing {action} ({index}/{total})...")
        if action in _PORTABLE_SPECS:
            installed = _install_portable(
                action, tools_dir=tools_dir, downloads_dir=downloads_dir,
                download=download, run=run, log=log, cancelled=cancelled)
            if action == "llvm":
                result["clang"], result["clangxx"] = installed
            else:
                result[action] = installed
        elif action == "vs":
            installer_output = _install_vs_build_tools(
                downloads_dir=downloads_dir, download=download,
                run=run, log=log, cancelled=cancelled)
            _check_cancelled(cancelled)
            pair = _resolve_msvc(run, log)
            if pair is None:
                tail = str(installer_output)[-2000:] if installer_output else "<no output>"
                raise RuntimeError(
                    "VS Build Tools installer finished but no usable VS2022 "
                    f"MSVC linker/Windows SDK was detected. Installer output: {tail}"
                )
            result["msvc"], result["rc"] = pair
        elif action == "redist":
            installer_output = _install_vc_redist(
                downloads_dir=downloads_dir, download=download,
                run=run, log=log, cancelled=cancelled)
            _check_cancelled(cancelled)
            still = _missing_core_runtimes()
            if still:
                tail = str(installer_output)[-2000:] if installer_output else "<no output>"
                raise RuntimeError(
                    "VC++ Redistributable installer finished but these runtimes "
                    f"are still missing: {', '.join(still)}. Installer output: {tail}"
                )

    _check_cancelled(cancelled)
    problems = []
    if not _tool_ok(run, result["git"], MIN_GIT):
        problems.append("git")
    if not _tool_ok(run, result["cmake"], MIN_CMAKE):
        problems.append("cmake")
    if not _tool_ok(run, result["ninja"], MIN_NINJA):
        problems.append("ninja")
    if not (_tool_ok(run, result["clang"], MIN_CLANG)
            and _tool_ok(run, result["clangxx"], MIN_CLANG)):
        problems.append("clang")
    if not _validate_msvc_pair(result["msvc"], result["rc"]):
        problems.extend(["msvc", "rc"])
    if problems:
        raise RuntimeError(
            "Tool bootstrap finished but these tools are still unusable: "
            + ", ".join(sorted(set(problems)))
        )
    _progress_to(progress, total, total, "Tool bootstrap complete.")
    return result


def bootstrap_consent(found=None):
    missing = [key for key in TOOL_KEYS if not (found or {}).get(key)]
    detected = "Not detected: " + ", ".join(missing) + ".\n\n" if missing else ""
    return (
        "Build PES6 from your own PAL ISO?\n\n"
        + detected
        + "Tool versions are checked automatically. Missing or outdated tools "
        "are downloaded from official sources; downloads can be several GB.\n\n"
        "Portable tools are temporary. Signed Microsoft C++/SDK/runtime installers "
        "may ask for Windows administrator approval and remain as shared components. "
        "Continuing accepts the required vendor licenses.\n\n"
        "Your ISO and existing files are preserved. Temporary build files are removed. "
        "Cancelling waits for an in-progress shared Windows install to finish safely.\n\n"
        "Continue?"
    )
