#pragma once

#include <filesystem>
#include <memory>

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

  void OnPostSetup() override {
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
    settings_.reset();
    pes6::StopGameDataPrefetch();
  }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    if (config.gpu_plugin.empty()) {
      config.gpu_plugin = "xenos";
    }
    rex::cvar::SetFlagByName("mnk_mode", "true");
  }

 private:
  std::filesystem::path game_data_root_;
  std::filesystem::path config_path_;
  std::unique_ptr<pes6::Pes6SettingsDialog> settings_;
};
