// PES6 native settings overlay, opened with F5.
#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <string_view>

#include <rex/ui/imgui_dialog.h>

namespace pes6 {

class Pes6SettingsDialog final : public rex::ui::ImGuiDialog {
 public:
  Pes6SettingsDialog(rex::ui::ImGuiDrawer* imgui_drawer,
                     std::filesystem::path config_path);
  ~Pes6SettingsDialog() override;

 protected:
  void OnDraw(ImGuiIO& io) override;

 private:
  void DrawVideo();
  void DrawPerformance();
  void DrawInput();
  void DrawAbout();
  void DrawPresets();
  void DrawController(ImGuiIO& io);
  bool DrawBool(const char* name, const char* label);
  bool DrawInt(const char* name, const char* label, int minimum, int maximum);
  bool DrawCombo(const char* name, const char* label,
                 const char* const* items, int item_count);
  bool Set(const char* name, std::string_view value);
  void ApplyPreset(bool quality);

  std::filesystem::path config_path_;
  bool open_ = true;
  bool saved_ = false;
  uint64_t previous_buttons_ = 0;
  int last_dx_ = 0;
  int last_dy_ = 0;
  std::chrono::steady_clock::time_point next_repeat_{};
};

}  // namespace pes6
