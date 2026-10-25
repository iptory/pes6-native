# Third-party notices

This repository does not vendor the ReXGlue SDK. `BUILD.md` fetches the
pinned upstream revision and applies `patches/rexglue-sdk.patch`. The SDK and
its dependencies keep their upstream licenses.

The patch is a modified work of ReXGlue and remains under the ReXGlue BSD
3-Clause license. The full notice for ReXGlue and its Xenia-derived portions
is reproduced below.

## ReXGlue and Xenia

```text
Copyright (c) 2026, Tom Clay <tomc@tctechstuff.com>

Portions of this software are derived from the Xenia project:
Copyright (c) 2022, Ben Vanik and Xenia project contributors
https://xenia.jp

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Dependencies fetched with ReXGlue

The exact license files are in the pinned SDK checkout under
`thirdparty/`. This table is a summary, not a replacement for those files.

| Component | License | Upstream |
| --- | --- | --- |
| ReXGlue SDK | BSD-3-Clause | https://github.com/rexglue/rexglue-sdk |
| Xenia-derived runtime portions | BSD-3-Clause | https://github.com/xenia-project/xenia |
| SDL3 | zlib | https://github.com/libsdl-org/SDL |
| FFmpeg | LGPL-2.1-or-later | https://ffmpeg.org/ |
| libmspack | LGPL-2.1 | https://github.com/kyz/libmspack |
| Dear ImGui | MIT | https://github.com/ocornut/imgui |
| fmt | MIT | https://github.com/fmtlib/fmt |
| spdlog | MIT | https://github.com/gabime/spdlog |
| toml++ | MIT | https://github.com/marzer/tomlplusplus |
| inja | MIT | https://github.com/pantor/inja |
| utfcpp | Boost Software License 1.0 | https://github.com/nemtrif/utfcpp |
| xxHash | BSD-2-Clause | https://github.com/Cyan4973/xxHash |
| o1heap | MIT | https://github.com/pavel-kirienko/o1heap |
| SIMDe | MIT | https://github.com/simd-everywhere/simde |
| CLI11 | BSD-3-Clause | https://github.com/CLIUtils/CLI11 |
| Catch2 | Boost Software License 1.0 | https://github.com/catchorg/Catch2 |
| Tracy | BSD-3-Clause | https://github.com/wolfpld/tracy |
| Vulkan-Headers and Vulkan-Loader | Apache-2.0 and MIT | https://github.com/KhronosGroup/Vulkan-Headers |
| Vulkan Memory Allocator | MIT | https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator |
| DirectXShaderCompiler headers | MIT | https://github.com/microsoft/DirectXShaderCompiler |

FFmpeg and libmspack are used under the LGPL. A binary distribution must
provide the corresponding source and notices required by those licenses. This
repository is source only and does not include a binary or a relinked SDK.

## Microsoft components

Microsoft Visual C++ runtime DLLs, the Windows SDK, and DirectX components are
not included in this repository. If you redistribute Microsoft runtime files,
follow Microsoft's redistribution terms.

## Game material

Pro Evolution Soccer 6, its code, its data, and its trademarks are property of
Konami and their respective owners. No game material is licensed by this
repository.


## PES6Builder

The optional prebuilt `PES6Builder.exe` setup program embeds Python, Tcl/Tk,
Pillow, and PyInstaller. It contains no game code. Its notices are in
`builder/NOTICES.md`.
