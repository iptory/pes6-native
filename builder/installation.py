"""Disposable build workspace plus atomic final publish.

One build run owns exactly one directory: ``<output>/.pes6-work-*``. All
checkouts, CMake outputs, downloads and logs live inside it. The finished
game is assembled in ``<workspace>/PES6`` and published with a single
same-volume rename to ``<output>/PES6``.

Parent flow: ``check_destination_free()`` BEFORE any download, then build,
move ``<workspace>/game`` into staging, write config/launcher/notices,
``publish()``, finally ``close()``. Parent also owns the Windows
process tree (suspended spawn in a KILL_ON_JOB_CLOSE job, kill on cancel)
and the SDK ``log_to_file`` default; this module only writes the key.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

FINAL_DIR_NAME = "PES6"
WORKSPACE_PREFIX = ".pes6-work-"
NOTICE_FILES = ("THIRD_PARTY_NOTICES.md", "LICENSE")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class InstallationError(RuntimeError):
    """Base error for workspace and publish failures."""


class DestinationExistsError(InstallationError):
    """The final destination is occupied; nothing was overwritten."""

    def __init__(self, destination: Path):
        self.destination = Path(destination)
        super().__init__(
            f"Destination already exists and is not empty: {destination}. "
            "The existing installation (settings, saves, caches) was left "
            "untouched. Choose a different output folder."
        )


class WorkspaceCleanupError(InstallationError):
    """Workspace removal failed; the leftover path is reported, never hidden."""

    def __init__(self, workspace: Path, published: Path | None = None):
        self.workspace = Path(workspace)
        self.published = Path(published) if published is not None else None
        intact = (
            f"The published game at {self.published} is intact; "
            if self.published is not None
            else ""
        )
        super().__init__(
            f"Build workspace could not be removed: {workspace}. {intact}"
            "Close any program using files inside it (Windows locks open "
            "files), then delete the folder manually."
        )


def _is_special(path: str | Path) -> bool:
    """True for symlinks, reparse points/junctions, and mount points."""
    if os.path.islink(path):
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False  # missing paths are never special
    if attrs & _REPARSE_POINT:
        return True
    try:
        return bool(os.path.ismount(path))
    except OSError:
        return False


def _key(path: str | Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return os.path.normcase(value)


def _is_within(path: str | Path, root: str | Path) -> bool:
    try:
        key, base = _key(path), _key(root)
    except OSError:
        return False
    return key == base or key.startswith(base + os.sep)


def _removal_path(path: Path) -> str:
    value = os.path.abspath(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def _onerror_scoped(func, path: str | Path, exc_info, root: str | Path) -> None:
    """Retry read-only files in scope: add the write bit, keep other bits."""
    if _is_special(path) or not _is_within(path, root):
        raise exc_info[1]
    try:
        mode = os.stat(path).st_mode
    except OSError:
        raise exc_info[1]
    if mode & stat.S_IWRITE:
        raise exc_info[1]  # already writable: a lock, not a mode bit
    try:
        os.chmod(path, mode | stat.S_IWRITE)
    except OSError:
        raise exc_info[1]
    func(path)


def _is_free(dest: Path) -> bool:
    """True for a missing path or an empty real directory only."""
    if not os.path.lexists(dest):
        return not _is_special(dest)
    if _is_special(dest) or not dest.is_dir():
        return False
    try:
        next(dest.iterdir())
    except StopIteration:
        return True
    except OSError:
        return False
    return False


class BuildSession:
    """Owns one disposable workspace under the chosen output folder."""

    def __init__(self, output: str | Path):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        self._output = Path(os.path.abspath(output))
        self._workspace = Path(
            tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=str(self._output))
        )
        self._closed = False
        self._published: Path | None = None

    @property
    def output(self) -> Path:
        return self._output

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def staging(self) -> Path:
        return self._workspace / FINAL_DIR_NAME

    @property
    def destination(self) -> Path:
        return self._output / FINAL_DIR_NAME

    @property
    def published(self) -> Path | None:
        return self._published

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> BuildSession:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False

    def close(self) -> None:
        """Remove exactly this session's workspace. Idempotent on success.

        `_closed` is set only after successful removal, so a locked-file
        failure can be retried once the process tree is gone. A published
        destination is never touched.
        """
        if self._closed:
            return
        if _is_special(self._workspace) or _key(self._workspace) == _key(self._output):
            raise WorkspaceCleanupError(self._workspace, self._published)
        try:
            shutil.rmtree(
                _removal_path(self._workspace),
                onerror=lambda func, path, exc: _onerror_scoped(
                    func, path, exc, self._workspace),
            )
        except FileNotFoundError:
            if os.path.lexists(self._workspace):
                raise WorkspaceCleanupError(self._workspace, self._published)
        except OSError as error:
            raise WorkspaceCleanupError(self._workspace, self._published) from error
        self._closed = True

    def check_destination_free(self) -> Path:
        """Fail fast when the final destination is already occupied."""
        if self._closed:
            raise InstallationError("Build session is closed.")
        if not _is_free(self.destination):
            raise DestinationExistsError(self.destination)
        return self.destination

    def publish(self) -> Path:
        """Rename staging to the destination. Refuses occupied targets."""
        if self._closed:
            raise InstallationError("Build session is closed; cannot publish.")
        staging = self.staging
        if not staging.is_dir():
            raise InstallationError(
                f"Nothing staged to publish: {staging} does not exist. "
                "Move the built game into the staging folder first."
            )
        dest = self.check_destination_free()
        if dest.is_dir():
            try:
                dest.rmdir()
            except OSError as error:
                raise InstallationError(
                    f"Cannot reuse empty destination {dest}: {error}."
                ) from error
        try:
            os.rename(staging, dest)
        except OSError as error:
            raise InstallationError(
                f"Could not publish {staging} to {dest}: {error}. "
                "The staging folder is unchanged."
            ) from error
        self._published = dest
        return dest


def write_runtime_config(staging: str | Path, final_game_path: str | Path) -> Path:
    """Write a fresh ``pes6.toml`` pointing at the final game location."""
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    config = staging / "pes6.toml"
    config.write_text(
        "# PES6 native runtime configuration\n"
        "mnk_mode = true\n"
        "log_to_file = false\n"
        "game_data_root = " + json.dumps(str(final_game_path), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return config


def write_launcher(staging: str | Path) -> Path:
    """Write ``Play PES6.cmd`` using only `%~dp0`-relative paths."""
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    launcher = staging / "Play PES6.cmd"
    launcher.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\"%~dp0pes6.exe\" --game_data_root=\"%~dp0game\"\r\n",
        encoding="ascii", newline="",
    )
    return launcher


def _notice_source(source_root: Path, dest_name: str) -> Path | None:
    if dest_name == "LICENSE":
        candidates = (
            source_root / "LICENSE",
            source_root / "licenses" / "PROJECT.txt",
            source_root / "licenses" / "LICENSE",
        )
    else:
        candidates = (
            source_root / dest_name,
            source_root / "licenses" / dest_name,
        )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def copy_notices(source_root: str | Path, staging: str | Path) -> list[str]:
    """Copy THIRD_PARTY_NOTICES.md + LICENSE; builder licenses excluded."""
    source_root = Path(source_root)
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in NOTICE_FILES:
        source = _notice_source(source_root, name)
        if source is None:
            raise InstallationError(
                f"Required legal notice for {name} is missing under {source_root}."
            )
        shutil.copy2(source, staging / name)
        copied.append(name)
    return copied
