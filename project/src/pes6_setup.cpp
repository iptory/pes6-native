// PES6 first-run setup: select the user's own ISO or an extracted game folder.
//
// The ISO path only extracts the XDVDFS game partition. The retail default.xex
// stays encrypted in place; the native recompiled code executes, so no XEX
// decryption key is needed or shipped. Game data is written locally and is
// never part of the distributable.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include "pes6_setup.h"

#include <windows.h>
#include <bcrypt.h>
#include <commdlg.h>
#include <knownfolders.h>
#include <objbase.h>
#include <shlobj.h>
#include <shobjidl.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_set>
#include <vector>

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "comdlg32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")

namespace pes6 {
namespace {

namespace fs = std::filesystem;

constexpr uint32_t kSector = 2048;
constexpr uint32_t kDescriptorSector = 32;
constexpr uint64_t kKnownPartition = 0xFD90000ull;
constexpr char kMagic[] = "MICROSOFT*XBOX*MEDIA";
constexpr size_t kMagicLen = sizeof(kMagic) - 1;
constexpr uint64_t kExpectedXexSize = 14331904ull;
constexpr char kExpectedXexHash[] =
    "b76db0fc2f86794f4e99bb4d93dd8148d31a00b518cb1e5c822ef0bc75ac1087";

std::wstring ToWide(std::string_view s) {
  if (s.empty()) {
    return {};
  }
  const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(),
                                    static_cast<int>(s.size()), nullptr, 0);
  if (n <= 0) {
    return {};
  }
  std::wstring out(static_cast<size_t>(n), L'\0');
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(),
                      static_cast<int>(s.size()), out.data(), n);
  return out;
}

std::string ToUtf8(std::wstring_view w) {
  if (w.empty()) {
    return {};
  }
  const int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, w.data(),
                                    static_cast<int>(w.size()), nullptr, 0,
                                    nullptr, nullptr);
  if (n <= 0) {
    return {};
  }
  std::string out(static_cast<size_t>(n), '\0');
  WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, w.data(),
                      static_cast<int>(w.size()), out.data(), n, nullptr,
                      nullptr);
  return out;
}

fs::path PathFromUtf8(std::string_view s) { return fs::path(ToWide(s)); }

std::string PathToUtf8(const fs::path& p) { return ToUtf8(p.wstring()); }

void ShowInfo(const std::wstring& text) {
  MessageBoxW(nullptr, text.c_str(), L"PES6 Setup",
              MB_OK | MB_ICONINFORMATION);
}

void ShowError(const std::wstring& text) {
  MessageBoxW(nullptr, text.c_str(), L"PES6 Setup", MB_OK | MB_ICONERROR);
}

fs::path LocalAppDataDir() {
  PWSTR raw = nullptr;
  if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT,
                                     nullptr, &raw))) {
    fs::path result(raw);
    CoTaskMemFree(raw);
    return result;
  }
  return {};
}

fs::path ConfigPath(const fs::path& exe_dir) {
  const fs::path local = LocalAppDataDir();
  if (!local.empty()) {
    return local / L"PES6Native" / L"game_path.txt";
  }
  return exe_dir / L"pes6_game_path.txt";
}

void WriteConfig(const fs::path& exe_dir, const fs::path& game_dir) {
  const fs::path cfg = ConfigPath(exe_dir);
  std::error_code ec;
  fs::create_directories(cfg.parent_path(), ec);
  std::ofstream out(cfg, std::ios::binary | std::ios::trunc);
  if (out) {
    out << PathToUtf8(game_dir) << '\n';
  }
}

fs::path ReadConfig(const fs::path& exe_dir) {
  const fs::path cfg = ConfigPath(exe_dir);
  std::ifstream in(cfg, std::ios::binary);
  if (!in) {
    return {};
  }
  std::string line;
  std::getline(in, line);
  if (!line.empty() && line.back() == '\r') {
    line.pop_back();
  }
  if (line.empty()) {
    return {};
  }
  return PathFromUtf8(line);
}

bool IsGameDataInternal(const fs::path& root) {
  std::error_code ec;
  if (root.empty() || !fs::is_directory(root, ec)) {
    return false;
  }
  const fs::path xex = root / L"default.xex";
  if (!fs::is_regular_file(xex, ec)) {
    return false;
  }
  const auto size = fs::file_size(xex, ec);
  return !ec && size > 0;
}

uint16_t ReadLE16(const uint8_t* p) {
  return static_cast<uint16_t>(p[0]) |
         static_cast<uint16_t>(static_cast<uint16_t>(p[1]) << 8);
}

uint32_t ReadLE32(const uint8_t* p) {
  return static_cast<uint32_t>(p[0]) |
         (static_cast<uint32_t>(p[1]) << 8) |
         (static_cast<uint32_t>(p[2]) << 16) |
         (static_cast<uint32_t>(p[3]) << 24);
}

struct Entry {
  std::string name;
  bool is_dir = false;
  uint32_t sector = 0;
  uint32_t size = 0;
  uint8_t attr = 0;
};

class Xdvdfs {
 public:
  bool Open(const fs::path& iso, std::string& error) {
    file_.open(iso, std::ios::binary);
    if (!file_) {
      error = "Cannot open the selected ISO.";
      return false;
    }
    file_.seekg(0, std::ios::end);
    const std::streamoff end = file_.tellg();
    if (end <= 0) {
      error = "Cannot determine the ISO size.";
      return false;
    }
    size_ = static_cast<uint64_t>(end);
    file_.seekg(0, std::ios::beg);
    return true;
  }

  bool FindPartition(uint64_t& partition, std::string& error) {
    if (kKnownPartition + 0x10000ull <= size_ &&
        ValidateDescriptor(kKnownPartition)) {
      partition = kKnownPartition;
      return true;
    }

    constexpr size_t kChunk = 16u << 20;
    std::vector<uint8_t> buffer(kChunk + kMagicLen);
    uint64_t offset = 0;
    size_t carry = 0;
    while (offset < size_) {
      const size_t want = static_cast<size_t>(
          std::min<uint64_t>(kChunk, size_ - offset));
      if (!ReadAt(offset, buffer.data() + carry, want)) {
        error = "Cannot read the ISO.";
        return false;
      }
      const size_t total = carry + want;
      size_t search = 0;
      while (search + kMagicLen <= total) {
        auto first = buffer.begin() + static_cast<std::ptrdiff_t>(search);
        auto last = buffer.begin() + static_cast<std::ptrdiff_t>(total);
        auto found = std::search(first, last, kMagic, kMagic + kMagicLen);
        if (found == last) {
          break;
        }
        const size_t index =
            static_cast<size_t>(found - buffer.begin());
        const uint64_t absolute = offset - carry + index;
        if (absolute >=
            static_cast<uint64_t>(kDescriptorSector) * kSector) {
          const uint64_t candidate =
              absolute -
              static_cast<uint64_t>(kDescriptorSector) * kSector;
          if (ValidateDescriptor(candidate)) {
            partition = candidate;
            return true;
          }
        }
        search = index + 1;
      }
      const size_t keep = std::min<size_t>(kMagicLen - 1, total);
      std::memmove(buffer.data(), buffer.data() + total - keep, keep);
      carry = keep;
      offset += want;
    }
    error = "No XDVDFS game partition was found in this ISO.";
    return false;
  }

  using ProgressFn =
      std::function<void(uint64_t done, uint64_t total, const fs::path& path)>;
  using CancelFn = std::function<bool()>;

  bool Extract(uint64_t partition, const fs::path& out_dir,
               const ProgressFn& progress, const CancelFn& cancelled,
               uint64_t& total_bytes, std::string& error) {
    uint32_t root_sector = 0;
    uint32_t root_size = 0;
    if (!ReadDescriptor(partition, root_sector, root_size)) {
      error = "The XDVDFS volume descriptor is invalid.";
      return false;
    }

    struct DirWork {
      std::string prefix;
      uint32_t sector = 0;
      uint32_t size = 0;
    };

    std::vector<DirWork> dirs;
    dirs.push_back({"", root_sector, root_size});
    std::vector<Entry> entries;

    while (!dirs.empty()) {
      const DirWork dir = dirs.back();
      dirs.pop_back();
      std::vector<Entry> children;
      if (!WalkDir(partition, dir.sector, dir.size, children, error)) {
        return false;
      }
      for (const Entry& child : children) {
        Entry entry = child;
        entry.name = dir.prefix + child.name;
        entries.push_back(entry);
        if (child.is_dir && (child.sector != 0 || child.size != 0)) {
          dirs.push_back({entry.name + "/", child.sector, child.size});
        }
      }
    }

    std::sort(entries.begin(), entries.end(),
              [](const Entry& a, const Entry& b) { return a.name < b.name; });

    total_bytes = 0;
    for (const Entry& entry : entries) {
      if (!entry.is_dir) {
        total_bytes += entry.size;
      }
    }

    std::error_code ec;
    fs::create_directories(out_dir, ec);
    if (ec) {
      error = "Cannot create the game-data directory.";
      return false;
    }

    uint64_t done = 0;
    for (const Entry& entry : entries) {
      if (cancelled && cancelled()) {
        error = "Extraction was cancelled.";
        return false;
      }

      fs::path relative;
      if (!SafeRelativePath(entry.name, relative, error)) {
        return false;
      }
      const fs::path destination = out_dir / relative;

      if (entry.is_dir) {
        fs::create_directories(destination, ec);
        continue;
      }

      fs::create_directories(destination.parent_path(), ec);
      std::ofstream file(destination, std::ios::binary | std::ios::trunc);
      if (!file) {
        error = "Cannot create an extracted game file.";
        return false;
      }

      uint64_t remaining = entry.size;
      uint64_t source =
          partition + static_cast<uint64_t>(entry.sector) * kSector;
      if (source > size_ || remaining > size_ - source) {
        error = "A game file points outside the ISO.";
        return false;
      }

      std::vector<uint8_t> chunk(8u << 20);
      while (remaining > 0) {
        const size_t want = static_cast<size_t>(
            std::min<uint64_t>(remaining, chunk.size()));
        if (!ReadAt(source, chunk.data(), want)) {
          error = "A short read occurred while extracting the ISO.";
          return false;
        }
        file.write(reinterpret_cast<const char*>(chunk.data()),
                   static_cast<std::streamsize>(want));
        if (!file) {
          error = "Cannot write an extracted game file.";
          return false;
        }
        source += want;
        remaining -= want;
        done += want;
        if (progress) {
          progress(done, total_bytes, destination);
        }
        if (cancelled && cancelled()) {
          error = "Extraction was cancelled.";
          return false;
        }
      }
    }
    return true;
  }

 private:
  bool ReadAt(uint64_t offset, void* destination, size_t length) {
    if (offset > size_ || length > size_ - offset) {
      return false;
    }
    file_.clear();
    file_.seekg(static_cast<std::streamoff>(offset), std::ios::beg);
    if (!file_) {
      return false;
    }
    file_.read(reinterpret_cast<char*>(destination),
               static_cast<std::streamsize>(length));
    return file_.gcount() == static_cast<std::streamsize>(length);
  }

  bool ReadDescriptor(uint64_t partition, uint32_t& root_sector,
                      uint32_t& root_size) {
    std::array<uint8_t, kSector> descriptor{};
    if (!ReadAt(partition + static_cast<uint64_t>(kDescriptorSector) * kSector,
                descriptor.data(), descriptor.size())) {
      return false;
    }
    if (std::memcmp(descriptor.data(), kMagic, kMagicLen) != 0) {
      return false;
    }
    root_sector = ReadLE32(descriptor.data() + 20);
    root_size = ReadLE32(descriptor.data() + 24);
    return root_size != 0 && root_size <= 64u * 1024u * 1024u;
  }

  bool ValidateDescriptor(uint64_t partition) {
    uint32_t root_sector = 0;
    uint32_t root_size = 0;
    if (!ReadDescriptor(partition, root_sector, root_size)) {
      return false;
    }
    const uint64_t root_offset =
        partition + static_cast<uint64_t>(root_sector) * kSector;
    if (root_offset > size_ || root_size > size_ - root_offset) {
      return false;
    }
    std::array<uint8_t, 16> entry{};
    if (!ReadAt(root_offset, entry.data(), entry.size())) {
      return false;
    }
    const uint32_t start = ReadLE32(entry.data() + 4);
    const uint32_t file_size = ReadLE32(entry.data() + 8);
    const uint8_t name_length = entry[13];
    if (name_length == 0 || name_length > 255) {
      return false;
    }
    const uint64_t data_offset =
        partition + static_cast<uint64_t>(start) * kSector;
    if (data_offset > size_ || file_size > size_ - data_offset) {
      return false;
    }
    return true;
  }

  bool WalkDir(uint64_t partition, uint32_t sector, uint32_t size,
               std::vector<Entry>& entries, std::string& error) {
    if (size == 0) {
      return true;
    }
    if (size > 64u * 1024u * 1024u) {
      error = "An XDVDFS directory is unreasonably large.";
      return false;
    }
    std::vector<uint8_t> table(size);
    if (!ReadAt(partition + static_cast<uint64_t>(sector) * kSector,
                table.data(), table.size())) {
      error = "Cannot read an XDVDFS directory.";
      return false;
    }

    std::vector<uint32_t> stack;
    stack.push_back(0);
    std::unordered_set<uint32_t> seen;
    while (!stack.empty()) {
      const uint32_t offset = stack.back();
      stack.pop_back();
      if (offset > table.size() || table.size() - offset < 14 ||
          !seen.insert(offset).second) {
        continue;
      }
      const uint8_t* p = table.data() + offset;
      const uint16_t left = ReadLE16(p);
      const uint16_t right = ReadLE16(p + 2);
      const uint32_t start = ReadLE32(p + 4);
      const uint32_t file_size = ReadLE32(p + 8);
      const uint8_t attr = p[12];
      const uint8_t name_length = p[13];
      if (name_length == 0 ||
          table.size() - offset - 14 < name_length) {
        continue;
      }
      if (right != 0 && right != 0xFFFF) {
        stack.push_back(static_cast<uint32_t>(right) * 4);
      }
      if (left != 0 && left != 0xFFFF) {
        stack.push_back(static_cast<uint32_t>(left) * 4);
      }
      std::string name(reinterpret_cast<const char*>(p + 14),
                       name_length);
      if (name == "." || name == "..") {
        continue;
      }
      entries.push_back(
          {std::move(name), (attr & 0x10) != 0, start, file_size, attr});
    }
    return true;
  }

  bool SafeRelativePath(const std::string& path, fs::path& relative,
                        std::string& error) {
    size_t start = 0;
    while (start <= path.size()) {
      const size_t slash = path.find('/', start);
      const std::string component =
          path.substr(start, slash == std::string::npos
                                 ? std::string::npos
                                 : slash - start);
      if (component.empty() || component == "." || component == ".." ||
          component.find('\\') != std::string::npos ||
          component.find(':') != std::string::npos) {
        error = "The ISO contains an unsafe file path.";
        return false;
      }
      relative /= ToWide(component);
      if (slash == std::string::npos) {
        break;
      }
      start = slash + 1;
    }
    return !relative.empty();
  }

  std::ifstream file_;
  uint64_t size_ = 0;
};

bool Sha256File(const fs::path& file_path, std::string& hex,
                std::string& error) {
  BCRYPT_ALG_HANDLE algorithm = nullptr;
  NTSTATUS status = BCryptOpenAlgorithmProvider(
      &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0);
  if (!BCRYPT_SUCCESS(status)) {
    error = "Cannot initialize SHA-256.";
    return false;
  }

  DWORD object_length = 0;
  DWORD result_length = 0;
  status = BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                             reinterpret_cast<PUCHAR>(&object_length),
                             sizeof(object_length), &result_length, 0);
  if (!BCRYPT_SUCCESS(status)) {
    BCryptCloseAlgorithmProvider(algorithm, 0);
    error = "Cannot query SHA-256 state size.";
    return false;
  }

  std::vector<BYTE> object(object_length);
  BCRYPT_HASH_HANDLE hash = nullptr;
  status = BCryptCreateHash(algorithm, &hash, object.data(), object_length,
                            nullptr, 0, 0);
  if (!BCRYPT_SUCCESS(status)) {
    BCryptCloseAlgorithmProvider(algorithm, 0);
    error = "Cannot create SHA-256 state.";
    return false;
  }

  std::ifstream input(file_path, std::ios::binary);
  if (!input) {
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    error = "Cannot open default.xex for validation.";
    return false;
  }

  std::vector<char> buffer(1u << 20);
  while (input) {
    input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
    const std::streamsize read = input.gcount();
    if (read > 0) {
      status = BCryptHashData(
          hash, reinterpret_cast<PUCHAR>(buffer.data()),
          static_cast<ULONG>(read), 0);
      if (!BCRYPT_SUCCESS(status)) {
        input.close();
        BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(algorithm, 0);
        error = "Cannot hash default.xex.";
        return false;
      }
    }
  }
  if (!input.eof()) {
    input.close();
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    error = "Cannot read default.xex for validation.";
    return false;
  }

  std::array<BYTE, 32> digest{};
  status = BCryptFinishHash(hash, digest.data(),
                            static_cast<ULONG>(digest.size()), 0);
  BCryptDestroyHash(hash);
  BCryptCloseAlgorithmProvider(algorithm, 0);
  if (!BCRYPT_SUCCESS(status)) {
    error = "Cannot finish SHA-256.";
    return false;
  }

  static constexpr char kHex[] = "0123456789abcdef";
  hex.clear();
  hex.reserve(digest.size() * 2);
  for (const BYTE byte : digest) {
    hex.push_back(kHex[byte >> 4]);
    hex.push_back(kHex[byte & 0x0F]);
  }
  return true;
}

bool CheckXex(const fs::path& game_dir) {
  const fs::path xex = game_dir / L"default.xex";
  std::error_code ec;
  const uint64_t size = fs::file_size(xex, ec);
  bool mismatch = ec || size != kExpectedXexSize;
  if (!mismatch) {
    std::string hash;
    std::string error;
    if (!Sha256File(xex, hash, error) || hash != kExpectedXexHash) {
      mismatch = true;
    }
  }
  if (!mismatch) {
    return true;
  }
  const int answer = MessageBoxW(
      nullptr,
      L"The selected default.xex does not match the tested PAL PES6 "
      L"release.\n\nContinue anyway? The game may not run correctly.",
      L"PES6 Setup", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2);
  return answer == IDYES;
}

fs::path SelectIso() {
  wchar_t file[MAX_PATH] = L"";
  OPENFILENAMEW dialog{};
  dialog.lStructSize = sizeof(dialog);
  dialog.lpstrFilter =
      L"Xbox 360 ISO (*.iso)\0*.iso\0All files (*.*)\0*.*\0";
  dialog.lpstrFile = file;
  dialog.nMaxFile = MAX_PATH;
  dialog.lpstrTitle = L"Select your PES6 ISO";
  dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
  if (!GetOpenFileNameW(&dialog)) {
    return {};
  }
  return fs::path(file);
}

std::wstring EnvironmentValue(const wchar_t* name) {
  std::array<wchar_t, 32767> buffer{};
  const DWORD length =
      GetEnvironmentVariableW(name, buffer.data(),
                              static_cast<DWORD>(buffer.size()));
  if (length == 0 || length >= buffer.size()) {
    return {};
  }
  return std::wstring(buffer.data(), length);
}

fs::path SelectFolder() {
  IFileOpenDialog* dialog = nullptr;
  if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr,
                              CLSCTX_INPROC_SERVER,
                              IID_PPV_ARGS(&dialog))) ||
      dialog == nullptr) {
    return {};
  }
  DWORD options = 0;
  if (SUCCEEDED(dialog->GetOptions(&options))) {
    dialog->SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM |
                       FOS_PATHMUSTEXIST);
  }
  dialog->SetTitle(L"Select the extracted PES6 game folder");

  fs::path result;
  if (SUCCEEDED(dialog->Show(nullptr))) {
    IShellItem* item = nullptr;
    if (SUCCEEDED(dialog->GetResult(&item)) && item != nullptr) {
      PWSTR raw = nullptr;
      if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &raw)) &&
          raw != nullptr) {
        result = fs::path(raw);
        CoTaskMemFree(raw);
      }
      item->Release();
    }
  }
  dialog->Release();
  return result;
}

bool IsPlaceholderOnlyGameDir(const fs::path& dir) {
  std::error_code ec;
  if (!fs::is_directory(dir, ec)) {
    return false;
  }
  size_t count = 0;
  for (const auto& entry : fs::directory_iterator(dir, ec)) {
    if (ec) {
      return false;
    }
    ++count;
    if (entry.path().filename() != L"PUT_GAME_FILES_HERE.txt") {
      return false;
    }
  }
  return count <= 1;
}

fs::path ChooseExtractDir(const fs::path& exe_dir) {
  std::error_code ec;
  const fs::path exe_game = exe_dir / L"game";
  if (!fs::exists(exe_game, ec)) {
    return exe_game;
  }
  if (fs::is_directory(exe_game, ec) &&
      (fs::is_empty(exe_game, ec) || IsPlaceholderOnlyGameDir(exe_game))) {
    return exe_game;
  }
  const fs::path local = LocalAppDataDir();
  if (local.empty()) {
    return exe_game;
  }
  return local / L"PES6Native" / L"game";
}

bool ExtractIso(const fs::path& iso, const fs::path& output,
                std::wstring& error) {
  Xdvdfs image;
  std::string detail;
  if (!image.Open(iso, detail)) {
    error = ToWide(detail);
    return false;
  }

  uint64_t partition = 0;
  if (!image.FindPartition(partition, detail)) {
    error = ToWide(detail);
    return false;
  }

  std::error_code ec;
  fs::create_directories(output, ec);
  if (ec) {
    error = L"Cannot create the extraction directory.";
    return false;
  }

  IProgressDialog* progress_dialog = nullptr;
  if (SUCCEEDED(CoCreateInstance(CLSID_ProgressDialog, nullptr,
                                 CLSCTX_INPROC_SERVER,
                                 IID_PPV_ARGS(&progress_dialog))) &&
      progress_dialog != nullptr) {
    progress_dialog->SetTitle(L"PES6 Setup");
    progress_dialog->SetLine(1, L"Extracting game files...", FALSE, nullptr);
    progress_dialog->StartProgressDialog(nullptr, nullptr, PROGDLG_AUTOTIME,
                                        nullptr);
  }

  uint64_t total = 0;
  const bool ok = image.Extract(
      partition, output,
      [&](uint64_t done, uint64_t total_bytes, const fs::path& current) {
        if (progress_dialog != nullptr) {
          const std::wstring name = current.filename().wstring();
          progress_dialog->SetLine(1, name.c_str(), FALSE, nullptr);
          if (total_bytes > 0) {
            progress_dialog->SetProgress64(done, total_bytes);
          }
        }
      },
      [&]() {
        return progress_dialog != nullptr &&
               progress_dialog->HasUserCancelled() != FALSE;
      },
      total, detail);

  if (progress_dialog != nullptr) {
    progress_dialog->StopProgressDialog();
    progress_dialog->Release();
  }

  if (!ok) {
    error = ToWide(detail);
    return false;
  }
  return true;
}

struct ComScope {
  HRESULT result = E_FAIL;
  ComScope() {
    result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  }
  ~ComScope() {
    if (SUCCEEDED(result)) {
      CoUninitialize();
    }
  }
};

}  // namespace

bool IsGameDataValid(const fs::path& root) { return IsGameDataInternal(root); }

std::filesystem::path EnsureGameData(
    const std::filesystem::path& executable_folder) {
  const fs::path default_dir = executable_folder / L"game";
  if (IsGameDataValid(default_dir)) {
    return default_dir;
  }
  const fs::path configured = ReadConfig(executable_folder);
  if (IsGameDataValid(configured)) {
    return configured;
  }

  ComScope com;

  const std::wstring cli_folder = EnvironmentValue(L"PES6_SETUP_FOLDER");
  if (!cli_folder.empty()) {
    const fs::path folder(cli_folder);
    if (!IsGameDataValid(folder)) {
      ShowError(L"PES6_SETUP_FOLDER does not contain default.xex and the "
                L"extracted game data.");
      return {};
    }
    if (!CheckXex(folder)) {
      return {};
    }
    WriteConfig(executable_folder, folder);
    ShowInfo(L"Game data selected:\n" + folder.wstring() +
             L"\n\nStarting PES6.");
    return folder;
  }

  const std::wstring cli_iso = EnvironmentValue(L"PES6_SETUP_ISO");
  if (!cli_iso.empty()) {
    const fs::path iso(cli_iso);
    const std::wstring cli_out = EnvironmentValue(L"PES6_SETUP_OUT");
    const fs::path output =
        cli_out.empty() ? ChooseExtractDir(executable_folder)
                        : fs::path(cli_out);
    std::wstring extraction_error;
    if (!ExtractIso(iso, output, extraction_error)) {
      ShowError(L"Could not extract the PES6 ISO.\n\n" + extraction_error);
      return {};
    }
    if (!IsGameDataValid(output)) {
      ShowError(L"The ISO was extracted, but default.xex was not found.");
      return {};
    }
    if (!CheckXex(output)) {
      return {};
    }
    WriteConfig(executable_folder, output);
    ShowInfo(L"Extraction complete:\n" + output.wstring() +
             L"\n\nStarting PES6.");
    return output;
  }

  for (;;) {
    const int choice = MessageBoxW(
        nullptr,
        L"PES6 needs the original game data.\n\n"
        L"No game data is included. Use your own lawfully obtained PAL "
        L"Xbox 360 copy of PES6.\n\n"
        L"Yes   - select PES6.iso and extract the game files now\n"
        L"No    - select an already extracted game folder\n"
        L"Cancel - quit",
        L"PES6 Setup",
        MB_YESNOCANCEL | MB_ICONQUESTION | MB_DEFBUTTON1);
    if (choice == IDCANCEL) {
      ShowInfo(L"PES6 setup was cancelled. The game cannot start without "
               L"the original game data.");
      std::exit(EXIT_SUCCESS);
    }

    if (choice == IDNO) {
      const fs::path folder = SelectFolder();
      if (folder.empty()) {
        continue;
      }
      if (!IsGameDataValid(folder)) {
        ShowError(L"That folder does not contain default.xex (and the "
                  L"extracted game data). Select the folder that contains "
                  L"the extracted PES6 files.");
        continue;
      }
      if (!CheckXex(folder)) {
        continue;
      }
      WriteConfig(executable_folder, folder);
      ShowInfo(L"Game data selected:\n" + folder.wstring() +
               L"\n\nStarting PES6.");
      return folder;
    }

    if (choice == IDYES) {
      const fs::path iso = SelectIso();
      if (iso.empty()) {
        continue;
      }
      const fs::path output = ChooseExtractDir(executable_folder);
      std::wstring extraction_error;
      if (!ExtractIso(iso, output, extraction_error)) {
        ShowError(L"Could not extract the PES6 ISO.\n\n" + extraction_error);
        continue;
      }
      if (!IsGameDataValid(output)) {
        ShowError(L"The ISO was extracted, but default.xex was not found. "
                  L"Make sure this is the Xbox 360 PES6 disc.");
        continue;
      }
      if (!CheckXex(output)) {
        continue;
      }
      WriteConfig(executable_folder, output);
      ShowInfo(L"Extraction complete:\n" + output.wstring() +
               L"\n\nStarting PES6.");
      return output;
    }
  }
}

}  // namespace pes6
