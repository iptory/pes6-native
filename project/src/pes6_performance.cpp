#include "pes6_performance.h"

#include <rex/cvar.h>

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <system_error>
#include <thread>
#include <vector>

REXCVAR_DEFINE_BOOL(prefetch_game_data, true, "PES6/Performance",
                    "Warm the Windows file cache with PES6 game data at startup "
                    "(reduces first-load stalls; no gameplay effect)")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_INT32(prefetch_game_data_limit_mb, 0, "PES6/Performance",
                     "Prefetch budget in MiB (0 = all game-data files)")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace pes6 {
namespace {

std::jthread prefetch_thread;

}  // namespace

void StartGameDataPrefetch(const std::filesystem::path& game_root) {
  if (prefetch_thread.joinable() || !REXCVAR_GET(prefetch_game_data) ||
      game_root.empty()) {
    return;
  }

  const int32_t limit_mb = REXCVAR_GET(prefetch_game_data_limit_mb);
  const uint64_t budget =
      limit_mb <= 0 ? UINT64_MAX
                    : static_cast<uint64_t>(limit_mb) * 1024ull * 1024ull;

  prefetch_thread = std::jthread([root = game_root, budget](std::stop_token stop) {
    std::error_code ec;
    std::vector<std::filesystem::path> files;
    for (std::filesystem::recursive_directory_iterator it(root, ec), end;
         !ec && it != end; it.increment(ec)) {
      if (stop.stop_requested()) {
        return;
      }
      if (!it->is_regular_file(ec)) {
        continue;
      }
      files.push_back(it->path());
    }
    if (ec) {
      return;
    }

    // Large .img files dominate the first stadium load; process them first.
    std::sort(files.begin(), files.end(), [](const auto& a, const auto& b) {
      std::error_code ea;
      std::error_code eb;
      return std::filesystem::file_size(a, ea) >
             std::filesystem::file_size(b, eb);
    });

    std::vector<char> buffer(8u << 20);
    uint64_t remaining = budget;
    for (const auto& file : files) {
      if (stop.stop_requested() || remaining == 0) {
        return;
      }
      std::ifstream input(file, std::ios::binary);
      if (!input) {
        continue;
      }
      while (input && remaining > 0) {
        if (stop.stop_requested()) {
          return;
        }
        const size_t want = static_cast<size_t>(
            std::min<uint64_t>(remaining, buffer.size()));
        input.read(buffer.data(), static_cast<std::streamsize>(want));
        const std::streamsize read = input.gcount();
        if (read <= 0) {
          break;
        }
        remaining -= static_cast<uint64_t>(read);
        // Yield so the prefetch never starves the game or the UI thread.
        std::this_thread::yield();
      }
    }
  });
}

void StopGameDataPrefetch() {
  if (prefetch_thread.joinable()) {
    prefetch_thread.request_stop();
    prefetch_thread.join();
  }
}

}  // namespace pes6
