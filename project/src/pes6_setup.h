// PES6 first-run game-data setup.
//
// The setup accepts the user's own legally obtained PES6 ISO or an already
// extracted Xbox 360 game folder, extracts the XDVDFS filesystem locally, and
// returns the resulting game-data root. No retail game data is shipped.
#pragma once

#include <filesystem>

namespace pes6 {

// True when `root` contains the extracted Xbox 360 game data required by the
// recompiled runtime. The encrypted retail default.xex is kept as data; the
// native recompiled code executes, so no XEX decryption is performed here.
bool IsGameDataValid(const std::filesystem::path& root);

// Returns a usable game-data root, running the one-time setup wizard when
// necessary. May terminate the process if the user cancels setup.
std::filesystem::path EnsureGameData(
    const std::filesystem::path& executable_folder);

}  // namespace pes6
