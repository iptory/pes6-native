#!/usr/bin/env python3
"""PES6 Native setup and builder.

This program contains no PES6 code or assets. It takes an ISO supplied by the
user, extracts the game partition locally, downloads the pinned ReXGlue SDK and
the public project source, then builds the native game on the user's machine.
"""

import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None


VERSION = "1.0.0"
SOURCE_URL = "https://github.com/iptory/pes6-native/archive/refs/tags/v1.0.0.zip"
SDK_URL = "https://github.com/rexglue/rexglue-sdk.git"
SDK_COMMIT = "c94f5ebdcb3c9d1a460ca48e04f9758448f8d518"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def which(name):
    found = shutil.which(name)
    if found:
        return Path(found)
    for candidate in (
        Path("C:/Program Files/LLVM/bin") / name,
        Path("C:/Program Files/CMake/bin") / name,
        Path("C:/Program Files/Git/cmd") / name,
    ):
        if candidate.is_file():
            return candidate
    return None


def find_rc():
    candidates = glob.glob(r"C:/Program Files (x86)/Windows Kits/10/bin/*/x64/rc.exe")
    if not candidates:
        return None
    return Path(sorted(candidates)[-1])


def prereqs():
    return {
        "git": which("git.exe") or which("git"),
        "cmake": which("cmake.exe") or which("cmake"),
        "ninja": which("ninja.exe") or which("ninja"),
        "clang": which("clang.exe") or which("clang"),
        "clangxx": which("clang++.exe") or which("clang++"),
        "rc": find_rc(),
    }


class Builder(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PES6 Native Builder {VERSION}")
        self.geometry("860x660")
        self.minsize(760, 560)
        self.iso = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / "Documents" / "PES6NativeBuilder"))
        self.status = tk.StringVar(value="Checking prerequisites...")
        self.log_queue = queue.Queue()
        self.worker = None
        self.process = None
        self._build_ui()
        self.after(100, self._drain_log)
        self.after(200, self.check_prereqs)

    def _build_ui(self):
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="PES6 Native setup and builder", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "This tool contains no game code or game data. It extracts your own PAL ISO, "
                "downloads the pinned ReXGlue SDK and public project source, and builds the game locally."
            ),
            wraplength=820,
        ).pack(anchor="w", pady=(6, 14))

        prereq_frame = ttk.LabelFrame(frame, text="Prerequisites", padding=10)
        prereq_frame.pack(fill="x", pady=(0, 12))
        self.prereq_text = tk.Text(prereq_frame, height=7, wrap="word", relief="flat", background=self.cget("background"))
        self.prereq_text.pack(fill="x")
        self.prereq_text.configure(state="disabled")
        ttk.Button(prereq_frame, text="Check again", command=self.check_prereqs).pack(anchor="e", pady=(6, 0))

        iso_frame = ttk.Frame(frame)
        iso_frame.pack(fill="x", pady=4)
        ttk.Label(iso_frame, text="ISO or extracted game folder", width=30).pack(side="left")
        ttk.Entry(iso_frame, textvariable=self.iso).pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(iso_frame, text="Browse ISO", command=self.pick_iso).pack(side="left")
        ttk.Button(iso_frame, text="Browse folder", command=self.pick_folder).pack(side="left", padx=(6, 0))

        out_frame = ttk.Frame(frame)
        out_frame.pack(fill="x", pady=4)
        ttk.Label(out_frame, text="Build output folder", width=30).pack(side="left")
        ttk.Entry(out_frame, textvariable=self.output).pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(out_frame, text="Browse", command=self.pick_output).pack(side="left")

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 8))
        self.start_button = ttk.Button(actions, text="Build and install", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(actions, text="Open output folder", command=self.open_output).pack(side="left", padx=(8, 0))

        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=(6, 4))
        self.log = tk.Text(frame, height=16, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    def log(self, message):
        self.log_queue.put(message)

    def _drain_log(self):
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log.configure(state="normal")
            self.log.insert("end", message + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain_log)

    def set_status(self, message):
        self.status.set(message)

    def check_prereqs(self):
        found = prereqs()
        self.prereq_text.configure(state="normal")
        self.prereq_text.delete("1.0", "end")
        missing = []
        for name, path in found.items():
            if path:
                self.prereq_text.insert("end", f"OK  {name}: {path}\n")
            else:
                self.prereq_text.insert("end", f"MISSING  {name}\n")
                missing.append(name)
        self.prereq_text.insert(
            "end",
            "\nIf something is missing, install it and press Check again. Recommended winget commands:\n"
            "  winget install --id Git.Git -e\n"
            "  winget install --id Kitware.CMake -e\n"
            "  winget install --id Ninja-build.Ninja -e\n"
            "  winget install --id LLVM.LLVM -e\n"
            "  winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "
            "\"--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended\"\n",
        )
        self.prereq_text.configure(state="disabled")
        self.start_button.configure(state="normal" if not missing else "disabled")
        if missing:
            self.set_status("Install the missing prerequisites, then press Check again.")
        else:
            self.set_status("Ready. Select your ISO or extracted game folder.")

    def pick_iso(self):
        path = filedialog.askopenfilename(title="Select your PAL PES6 ISO", filetypes=[("Xbox 360 ISO", "*.iso"), ("All files", "*.*")])
        if path:
            self.iso.set(path)

    def pick_folder(self):
        path = filedialog.askdirectory(title="Select the folder containing default.xex")
        if path:
            self.iso.set(path)

    def pick_output(self):
        path = filedialog.askdirectory(title="Select a build output folder")
        if path:
            self.output.set(path)

    def open_output(self):
        path = Path(self.output.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        source = self.iso.get().strip()
        if not source:
            messagebox.showwarning("Missing source", "Select your ISO or an extracted game folder.")
            return
        if not Path(source).exists():
            messagebox.showerror("Missing source", "The selected ISO or folder does not exist.")
            return
        missing = [name for name, path in prereqs().items() if not path]
        if missing:
            messagebox.showerror("Prerequisites missing", "Install the missing prerequisites first: " + ", ".join(missing))
            return
        out = Path(self.output.get()).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        self.start_button.configure(state="disabled")
        self.worker = threading.Thread(target=self.build_worker, args=(Path(source), out), daemon=True)
        self.worker.start()

    def run(self, command, cwd=None, check=True):
        self.log("> " + " ".join(str(part) for part in command))
        kwargs = {
            "cwd": str(cwd) if cwd else None,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "creationflags": CREATE_NO_WINDOW,
        }
        self.process = subprocess.Popen(command, **kwargs)
        for line in self.process.stdout:
            line = line.rstrip()
            if line:
                self.log(line)
        code = self.process.wait()
        self.process = None
        if check and code != 0:
            raise RuntimeError(f"Command failed with exit code {code}: {command[0]}")

    def download(self, url, destination):
        self.log(f"Downloading {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "PES6Builder/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as output:
            shutil.copyfileobj(response, output)
        self.log(f"Downloaded {destination.name}")

    def prepare_source(self, out):
        if (out / "project" / "pes6_manifest.toml").is_file():
            return
        archive = out / "pes6-native-source.zip"
        self.download(SOURCE_URL, archive)
        extract = out / "_source_extract"
        if extract.exists():
            shutil.rmtree(extract)
        with zipfile.ZipFile(archive) as z:
            z.extractall(extract)
        roots = [p for p in extract.iterdir() if p.is_dir()]
        if not roots:
            raise RuntimeError("The source archive did not contain a project directory.")
        for item in roots[0].iterdir():
            target = out / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
        shutil.rmtree(extract)
        archive.unlink(missing_ok=True)

    def extract_game(self, source, out):
        if source.is_dir():
            if not (source / "default.xex").is_file():
                raise RuntimeError("The selected folder does not contain default.xex.")
            return source
        game = out / "game"
        if (game / "default.xex").is_file():
            return game
        self.log("Extracting the XDVDFS game partition. This reads your ISO locally.")
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        from xdvdfs_extract import Image as XdvdfsImage, extract, find_partition
        image = XdvdfsImage(str(source))
        try:
            partition = find_partition(image)
            extract(image, partition, str(game), manifest_path=str(out / "game_manifest.json"))
        finally:
            image.close()
        if not (game / "default.xex").is_file():
            raise RuntimeError("Extraction finished without default.xex.")
        return game

    def write_presets(self, sdk, project, tools):
        clang = tools["clang"]
        clangxx = tools.get("clangxx") or clang.with_name("clang++.exe")
        rc = tools["rc"]
        sdk_presets = {
            "version": 6,
            "configurePresets": [{
                "name": "win-amd64-local",
                "inherits": "win-amd64",
                "cacheVariables": {
                    "CMAKE_C_COMPILER": clang.as_posix(),
                    "CMAKE_CXX_COMPILER": clangxx.as_posix(),
                    "CMAKE_RC_COMPILER": rc.as_posix(),
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
                    "CMAKE_PREFIX_PATH": (sdk / "out/install/win-amd64-local").as_posix(),
                },
            }],
            "buildPresets": [{
                "name": "win-amd64-release-local",
                "configurePreset": "win-amd64-release-local",
            }],
        }
        (sdk / "CMakeUserPresets.json").write_text(json.dumps(sdk_presets, indent=2), encoding="utf-8")
        (project / "CMakeUserPresets.json").write_text(json.dumps(project_presets, indent=2), encoding="utf-8")

    def extract_icon(self, out, game):
        icon_out = out / "_icons"
        exe = out / "rexglue-sdk/out/install/win-amd64-local/bin/rexglue.exe"
        if not exe.is_file() or not PILImage:
            return
        icon_out.mkdir(parents=True, exist_ok=True)
        try:
            self.run([
                str(exe), "init", "--project-name", "icons", "--xex-path",
                str(game / "default.xex"), "achievements",
                str(game / "default.xex"), str(icon_out),
            ])
            title = icon_out / "icons/title.png"
            final_icon = out / "PES6/title.ico"
            if title.is_file():
                image = PILImage.open(title).convert("RGBA")
                image.save(final_icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
                self.log(f"Wrote original title icon to {final_icon}")
        except Exception as error:
            self.log(f"Icon extraction skipped: {error}")

    def write_launcher(self, out, game):
        pes6 = out / "PES6"
        pes6.mkdir(parents=True, exist_ok=True)
        config = pes6 / "pes6.toml"
        config.write_text(
            "# PES6 native runtime configuration\n"
            "mnk_mode = true\n"
            "game_data_root = " + json.dumps(str(game), ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        launcher = pes6 / "Play PES6.cmd"
        launcher.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            "set \"ROOT=%~dp0\"\r\n"
            "\"%ROOT%pes6.exe\" --game_data_root=\"" + str(game) + "\"\r\n",
            encoding="utf-8",
        )

    def build_worker(self, source, out):
        try:
            tools = prereqs()
            for tool in tools.values():
                if tool:
                    os.environ["PATH"] = str(tool.parent) + os.pathsep + os.environ.get("PATH", "")
            self.tools = tools
            self.set_status("Preparing source...")
            self.prepare_source(out)
            project = out / "project"
            game = self.extract_game(source, out)
            sdk = out / "rexglue-sdk"
            if not (sdk / ".git").is_dir():
                self.set_status("Downloading ReXGlue SDK...")
                self.run([str(self.tools["git"]), "clone", "--recursive", SDK_URL, str(sdk)])
            self.set_status("Checking out the pinned SDK revision...")
            self.run([str(tools["git"]), "checkout", SDK_COMMIT], cwd=sdk)
            self.run([str(tools["git"]), "submodule", "update", "--init", "--recursive"], cwd=sdk)
            patch = out / "patches/rexglue-sdk.patch"
            if patch.is_file():
                self.run([str(tools["git"]), "apply", str(patch)], cwd=sdk)
            self.write_presets(sdk, project, tools)
            self.set_status("Building ReXGlue SDK...")
            self.run([str(tools["cmake"]), "--preset", "win-amd64-local"], cwd=sdk)
            self.run([str(tools["cmake"]), "--build", "--preset", "win-amd64-local-release", "--target", "install"], cwd=sdk)
            self.set_status("Generating native game code...")
            rexglue = sdk / "out/install/win-amd64-local/bin/rexglue.exe"
            self.run([str(rexglue), "codegen", "pes6_manifest.toml", "--ignore-stamp"], cwd=project)
            self.set_status("Compiling the native game. This is the long step...")
            self.run([str(tools["cmake"]), "--preset", "win-amd64-release-local"], cwd=project)
            self.run([str(tools["cmake"]), "--build", "--preset", "win-amd64-release-local"], cwd=project)
            self.set_status("Assembling the final game...")
            build = project / "out/build/win-amd64-release-local"
            pes6 = out / "PES6"
            for name in ("pes6.exe", "rexruntime.dll", "rexgpu-xenos.dll"):
                shutil.copy2(build / name, pes6 / name)
            system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            for name in ("msvcp140.dll", "msvcp140_atomic_wait.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
                source_dll = system32 / name
                if source_dll.is_file():
                    shutil.copy2(source_dll, pes6 / name)
            self.write_launcher(out, game)
            self.extract_icon(out, game)
            self.set_status("Build complete. Launching the game...")
            self.log("Build complete: " + str(pes6 / "Play PES6.cmd"))
            subprocess.Popen([str(pes6 / "pes6.exe"), f"--game_data_root={game}"], cwd=str(pes6), creationflags=CREATE_NO_WINDOW)
        except Exception as error:
            self.log(traceback.format_exc())
            self.set_status("Build failed. See the log above.")
            messagebox.showerror("Build failed", str(error))
        finally:
            self.start_button.configure(state="normal")


if __name__ == "__main__":
    Builder().mainloop()
