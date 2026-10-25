#pragma once

#include <array>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <memory>
#include <optional>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

#include <rex/cvar.h>
#include <rex/filesystem.h>
#include <rex/rex_app.h>
#include <rex/ui/imgui_drawer.h>
#include <rex/ui/keybinds.h>

#include "pes6_performance.h"
#include "pes6_settings.h"
#include "pes6_setup.h"

class Pes6App : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<Pes6App>(new Pes6App(ctx, "pes6",
        PPCImageConfig));
  }

  // Defaults for a standalone install: game data lives in <exe dir>/game and
  // the Xenos GPU plugin / keyboard controller emulation are enabled.
  void OnConfigurePaths(rex::PathConfig& paths) override {
    const auto exe_dir = rex::filesystem::GetExecutableFolder();
    if (paths.game_data_root.empty()) {
      paths.game_data_root = exe_dir / "game";
    }
    if (!pes6::IsGameDataValid(paths.game_data_root)) {
      const auto selected = pes6::EnsureGameData(exe_dir);
      if (!selected.empty()) {
        paths.game_data_root = selected;
      }
    }
    game_data_root_ = paths.game_data_root;
    config_path_ = paths.config_path;
  }

  std::optional<rex::PathConfig> OnFinalizePaths(
      const rex::PathConfig& defaults,
      std::function<void(rex::PathConfig)> resume) override {
    (void)resume;
    // GPU plugin cvars register after SetupEnvironment loads the config.
    // Reload now so resolution, filtering, and cache options apply before the
    // runtime creates its graphics objects.
    if (!config_path_.empty()) {
      rex::cvar::LoadConfig(config_path_);
      // Normalize older configs that wrote raw Windows paths.
      rex::cvar::SaveConfig(config_path_);
    }
    return defaults;
  }

  void OnPostSetup() override {
    if (window()) {
      window()->SetTitle("Pro Evolution Soccer 2006");
      ApplyWindowIcon();
    }
    pes6::StartGameDataPrefetch(game_data_root_);
  }

  void OnCreateDialogs(rex::ui::ImGuiDrawer* drawer) override {
    rex::ui::RegisterBind("bind_pes6_settings", "F5",
                          "Toggle PES6 system settings", [this, drawer] {
                            if (settings_) {
                              settings_.reset();
                            } else {
                              settings_ = std::make_unique<pes6::Pes6SettingsDialog>(
                                  drawer, config_path_);
                            }
                          });
  }

  void OnShutdown() override {
    if (!config_path_.empty()) {
      rex::cvar::SaveConfig(config_path_);
    }
    settings_.reset();
    pes6::StopGameDataPrefetch();
#ifdef _WIN32
    if (window_icon_big_) {
      DestroyIcon(window_icon_big_);
      window_icon_big_ = nullptr;
    }
    if (window_icon_small_) {
      DestroyIcon(window_icon_small_);
      window_icon_small_ = nullptr;
    }
#endif
  }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    if (config.gpu_plugin.empty()) {
      config.gpu_plugin = "xenos";
    }
    rex::cvar::SetFlagByName("mnk_mode", "true");
  }

 private:
  void ApplyWindowIcon() {
#ifdef _WIN32
    HWND hwnd = static_cast<HWND>(window()->GetNativeWindowHandle());
    if (!hwnd) {
      return;
    }
    const auto exe_dir = rex::filesystem::GetExecutableFolder();
    const std::array<std::filesystem::path, 4> candidates = {
        exe_dir / "title.ico",
        game_data_root_ / "title.ico",
        game_data_root_ / "icons" / "title.ico",
        exe_dir / "icons" / "title.ico",
    };
    for (const auto& path : candidates) {
      HICON big = static_cast<HICON>(LoadImageW(
          nullptr, path.c_str(), IMAGE_ICON, GetSystemMetrics(SM_CXICON),
          GetSystemMetrics(SM_CYICON), LR_LOADFROMFILE));
      if (!big) {
        continue;
      }
      HICON small_icon = static_cast<HICON>(LoadImageW(
          nullptr, path.c_str(), IMAGE_ICON, GetSystemMetrics(SM_CXSMICON),
          GetSystemMetrics(SM_CYSMICON), LR_LOADFROMFILE));
      SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(big));
      if (small_icon) {
        SendMessageW(hwnd, WM_SETICON, ICON_SMALL,
                     reinterpret_cast<LPARAM>(small_icon));
      }
      window_icon_big_ = big;
      window_icon_small_ = small_icon;
      return;
    }
#endif
  }

  std::filesystem::path game_data_root_;
  std::filesystem::path config_path_;
#ifdef _WIN32
  HICON window_icon_big_ = nullptr;
  HICON window_icon_small_ = nullptr;
#endif
  std::unique_ptr<pes6::Pes6SettingsDialog> settings_;
};
