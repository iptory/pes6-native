// PES6 native performance helpers.
//
// The shader-storage cache is already loaded by ReXGlue before the first frame.
// The remaining practical startup improvement is to warm the Windows file
// cache for the game data so the first stadium, menu video and cutscene loads
// do not have to fault 3+ GiB from cold storage.
#pragma once

#include <filesystem>

namespace pes6 {

void StartGameDataPrefetch(const std::filesystem::path& game_root);
void StopGameDataPrefetch();

}  // namespace pes6
