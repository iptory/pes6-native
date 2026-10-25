#include "pes6_settings.h"

#include <rex/cvar.h>
#include <rex/ui/imgui_drawer.h>

#include <imgui.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>

#ifdef _WIN32
#include <windows.h>
#endif

namespace pes6 {
namespace {

const rex::cvar::FlagEntry* FindFlag(const char* name) {
  for (const auto& entry : rex::cvar::GetRegistry()) {
    if (entry.name == name) {
      return &entry;
    }
  }
  return nullptr;
}

std::string LifecycleBadge(const rex::cvar::FlagEntry& entry) {
  switch (entry.lifecycle) {
    case rex::cvar::Lifecycle::kHotReload:
      return {};
    case rex::cvar::Lifecycle::kRequiresRestart:
      return "  [restart]";
    case rex::cvar::Lifecycle::kInitOnly:
      return "  [init only]";
  }
  return {};
}

bool IsEditable(const rex::cvar::FlagEntry& entry) {
  return entry.lifecycle != rex::cvar::Lifecycle::kInitOnly;
}

#ifdef _WIN32
struct GamepadState {
  WORD buttons;
  BYTE left_trigger;
  BYTE right_trigger;
  SHORT thumb_lx;
  SHORT thumb_ly;
  SHORT thumb_rx;
  SHORT thumb_ry;
};
struct ControllerState {
  DWORD packet_number;
  GamepadState gamepad;
};
using XInputGetStateFn = DWORD(WINAPI*)(DWORD, ControllerState*);

XInputGetStateFn GetXInputState() {
  static XInputGetStateFn function = []() -> XInputGetStateFn {
    HMODULE library = LoadLibraryW(L"xinput1_4.dll");
    if (!library) {
      library = LoadLibraryW(L"xinput9_1_0.dll");
    }
    if (!library) {
      return nullptr;
    }
    return reinterpret_cast<XInputGetStateFn>(
        GetProcAddress(library, "XInputGetState"));
  }();
  return function;
}
#endif

struct PresetValue {
  const char* name;
  const char* value;
};

constexpr PresetValue kPerformancePreset[] = {
    {"resolution_scale", "1"},
    {"native_2x_msaa", "false"},
    {"anisotropic_override", "1"},
    {"present_effect", "bilinear"},
    {"vsync", "true"},
    {"prefetch_game_data", "true"},
    {"prefetch_game_data_limit_mb", "0"},
    {"store_shaders", "true"},
    {"waitable_swap_chain", "true"},
    {"waitable_swap_chain_max_latency", "1"},
};

constexpr PresetValue kQualityPreset[] = {
    {"resolution_scale", "2"},
    {"native_2x_msaa", "true"},
    {"anisotropic_override", "5"},
    {"present_effect", "bilinear"},
    {"vsync", "true"},
    {"prefetch_game_data", "true"},
    {"prefetch_game_data_limit_mb", "0"},
    {"store_shaders", "true"},
    {"waitable_swap_chain", "true"},
    {"waitable_swap_chain_max_latency", "1"},
};

}  // namespace

Pes6SettingsDialog::Pes6SettingsDialog(rex::ui::ImGuiDrawer* imgui_drawer,
                                       std::filesystem::path config_path)
    : ImGuiDialog(imgui_drawer), config_path_(std::move(config_path)) {}

Pes6SettingsDialog::~Pes6SettingsDialog() = default;

bool Pes6SettingsDialog::Set(const char* name, std::string_view value) {
  if (const auto* entry = FindFlag(name)) {
    return entry->setter(value);
  }
  return false;
}

bool Pes6SettingsDialog::DrawBool(const char* name, const char* label) {
  const auto* entry = FindFlag(name);
  if (!entry) {
    return false;
  }
  bool value = entry->getter() == "true";
  const bool editable = IsEditable(*entry);
  if (!editable) {
    ImGui::BeginDisabled();
  }
  ImGui::PushID(name);
  const bool changed = ImGui::Checkbox(label, &value);
  if (ImGui::IsItemHovered()) {
    ImGui::SetTooltip("%s%s", entry->description.c_str(),
                      LifecycleBadge(*entry).c_str());
  }
  if (changed && editable) {
    entry->setter(value ? "true" : "false");
  }
  ImGui::PopID();
  if (!editable) {
    ImGui::EndDisabled();
  }
  if (!LifecycleBadge(*entry).empty()) {
    ImGui::SameLine();
    ImGui::TextDisabled("%s", LifecycleBadge(*entry).c_str());
  }
  return changed;
}

bool Pes6SettingsDialog::DrawInt(const char* name, const char* label,
                                 int minimum, int maximum) {
  const auto* entry = FindFlag(name);
  if (!entry) {
    return false;
  }
  int value = std::atoi(entry->getter().c_str());
  const bool editable = IsEditable(*entry);
  if (!editable) {
    ImGui::BeginDisabled();
  }
  ImGui::PushID(name);
  ImGui::SetNextItemWidth(260.0f);
  const bool changed = ImGui::SliderInt(label, &value, minimum, maximum);
  if (ImGui::IsItemHovered()) {
    ImGui::SetTooltip("%s%s", entry->description.c_str(),
                      LifecycleBadge(*entry).c_str());
  }
  if (changed && editable) {
    entry->setter(std::to_string(value));
  }
  ImGui::PopID();
  if (!editable) {
    ImGui::EndDisabled();
  }
  if (!LifecycleBadge(*entry).empty()) {
    ImGui::SameLine();
    ImGui::TextDisabled("%s", LifecycleBadge(*entry).c_str());
  }
  return changed;
}

bool Pes6SettingsDialog::DrawCombo(const char* name, const char* label,
                                   const char* const* items, int item_count) {
  const auto* entry = FindFlag(name);
  if (!entry || item_count <= 0) {
    return false;
  }
  const std::string current_value = entry->getter();
  int current = 0;
  for (int i = 0; i < item_count; ++i) {
    if (current_value == items[i]) {
      current = i;
      break;
    }
  }
  const bool editable = IsEditable(*entry);
  if (!editable) {
    ImGui::BeginDisabled();
  }
  ImGui::PushID(name);
  ImGui::SetNextItemWidth(260.0f);
  const bool changed = ImGui::Combo(label, &current, items, item_count);
  if (ImGui::IsItemHovered()) {
    ImGui::SetTooltip("%s%s", entry->description.c_str(),
                      LifecycleBadge(*entry).c_str());
  }
  if (changed && editable) {
    entry->setter(items[current]);
  }
  ImGui::PopID();
  if (!editable) {
    ImGui::EndDisabled();
  }
  if (!LifecycleBadge(*entry).empty()) {
    ImGui::SameLine();
    ImGui::TextDisabled("%s", LifecycleBadge(*entry).c_str());
  }
  return changed;
}

void Pes6SettingsDialog::ApplyPreset(bool quality) {
  const PresetValue* values = quality ? kQualityPreset : kPerformancePreset;
  const size_t count = quality ? (sizeof(kQualityPreset) / sizeof(kQualityPreset[0]))
                               : (sizeof(kPerformancePreset) /
                                  sizeof(kPerformancePreset[0]));
  for (size_t i = 0; i < count; ++i) {
    Set(values[i].name, values[i].value);
  }
  saved_ = false;
}

void Pes6SettingsDialog::DrawPresets() {
  ImGui::TextUnformatted("Presets");
  ImGui::TextWrapped(
      "Performance keeps the native 1280x720 guest output and favors "
      "consistent frame time. Quality raises the internal scale and filtering "
      "for stronger GPUs. Both presets keep VSync and the shader/asset caches "
      "enabled.");
  if (ImGui::Button("Performance", ImVec2(140, 0))) {
    ApplyPreset(false);
  }
  ImGui::SameLine();
  if (ImGui::Button("Quality", ImVec2(140, 0))) {
    ApplyPreset(true);
  }
  ImGui::Separator();
}

void Pes6SettingsDialog::DrawVideo() {
  ImGui::TextUnformatted("Output and image quality");
  ImGui::TextWrapped(
      "The guest renders at 1280x720. Resolution scale multiplies the native "
      "render target; anisotropic filtering improves angled surfaces. "
      "Restart-only options are marked.");
  ImGui::Separator();

  DrawBool("vsync", "VSync");
  DrawBool("fullscreen", "Fullscreen");
  DrawInt("resolution_scale", "Resolution scale", 1, 4);
  DrawBool("native_2x_msaa", "Native 2x MSAA");
  DrawInt("anisotropic_override", "Anisotropic filtering", 0, 5);

  const char* effects[] = {"bilinear", "cas", "fsr"};
  DrawCombo("present_effect", "Presentation filter", effects, 3);

  ImGui::Separator();
  ImGui::TextUnformatted("Texture memory");
  DrawInt("texture_cache_memory_limit_soft", "Soft cache limit (MiB)", 128, 4096);
  DrawInt("texture_cache_memory_limit_hard", "Hard cache limit (MiB)", 256, 8192);
  ImGui::TextWrapped(
      "The soft limit lets old textures be evicted; the hard limit bounds "
      "worst-case residency. Defaults are tuned for 8 GiB GPUs.");
}

void Pes6SettingsDialog::DrawPerformance() {
  ImGui::TextUnformatted("Startup and streaming");
  ImGui::TextWrapped(
      "Shader translation and the persistent shader storage are loaded before "
      "the first frame. Prefetching game data warms the Windows file cache so "
      "first-use stadium, menu-video and cutscene reads do not fault from cold "
      "storage. It does not modify the game files.");
  ImGui::Separator();

  DrawBool("prefetch_game_data", "Prefetch game data at startup");
  DrawInt("prefetch_game_data_limit_mb", "Prefetch budget (MiB, 0 = all)", 0, 8192);
  DrawBool("store_shaders", "Persist and load shader cache");

  ImGui::Separator();
  ImGui::TextUnformatted("Frame pacing and latency");
  DrawBool("waitable_swap_chain", "Waitable swap chain (lower input latency)");
  DrawInt("waitable_swap_chain_max_latency", "Maximum queued frames", 1, 4);
  ImGui::TextWrapped(
      "The game's simulation remains at its original 60 Hz tick. VSync uses "
      "the display refresh; disabling it can reduce latency but may tear. "
      "The shader cache removes first-use pipeline translation stutter; the "
      "asset prefetch shortens cold loads. The waitable swap chain caps the "
      "number of frames queued in DXGI, reducing input latency without "
      "blocking on the display refresh. The native D3D12 Xenos command "
      "processor and background pipeline creation remain enabled.");
}

void Pes6SettingsDialog::DrawInput() {
  ImGui::TextUnformatted("Controls");
  ImGui::TextWrapped(
      "The virtual keyboard appears automatically for game text entry such as "
      "Master League player names. Navigate with the D-pad or left stick, "
      "press A to type, B to backspace, Start to accept, Back to cancel, and "
      "LB/RB to toggle Shift. The physical keyboard remains available.");
  ImGui::Separator();
  DrawBool("mnk_mode", "Keyboard/mouse controller emulation");
  ImGui::TextWrapped(
      "P2 is a second synthetic controller on guest user 1; assign it to AWAY "
      "in Player Settings for local two-player matches.");
}

void Pes6SettingsDialog::DrawAbout() {
  ImGui::TextUnformatted("PES6 native Windows x64 recompilation");
  ImGui::TextWrapped(
      "Ahead-of-time PowerPC recompilation with native Xbox services, Xenos "
      "GPU translation, shader translation, XMA/FFmpeg audio and Xbox input. "
      "No game data is included; the first-run setup imports your own PAL ISO "
      "or extracted game folder.");
  ImGui::Separator();
  ImGui::TextWrapped(
      "F5 toggles this PES6 settings overlay. F4 opens the full ReXGlue cvar "
      "editor. F3 opens frame diagnostics. The game's own System Settings "
      "remain unchanged and are still available from the top menu.");
}

void Pes6SettingsDialog::DrawController(ImGuiIO& io) {
#ifdef _WIN32
  const XInputGetStateFn get_state = GetXInputState();
  if (!get_state) {
    return;
  }
  ControllerState state{};
  if (get_state(0, &state) != 0) {
    previous_buttons_ = 0;
    last_dx_ = 0;
    last_dy_ = 0;
    return;
  }

  constexpr WORD kA = 0x1000;
  constexpr WORD kB = 0x2000;
  constexpr WORD kStart = 0x0010;
  constexpr WORD kBack = 0x0020;
  constexpr WORD kDpadUp = 0x0001;
  constexpr WORD kDpadDown = 0x0002;
  constexpr WORD kDpadLeft = 0x0004;
  constexpr WORD kDpadRight = 0x0008;
  constexpr SHORT kDeadzone = 7849;

  const WORD buttons = state.gamepad.buttons;
  const auto pressed = [&](WORD mask) {
    return (buttons & mask) != 0 && (previous_buttons_ & mask) == 0;
  };
  const auto press_key = [&](ImGuiKey key) {
    io.AddKeyEvent(key, true);
    io.AddKeyEvent(key, false);
  };

  if (pressed(kA) || pressed(kStart)) {
    press_key(ImGuiKey_Enter);
  }
  if (pressed(kB) || pressed(kBack)) {
    press_key(ImGuiKey_Escape);
  }

  int dx = 0;
  int dy = 0;
  if (buttons & kDpadLeft) {
    dx = -1;
  } else if (buttons & kDpadRight) {
    dx = 1;
  }
  if (buttons & kDpadUp) {
    dy = -1;
  } else if (buttons & kDpadDown) {
    dy = 1;
  }
  if (state.gamepad.thumb_lx < -kDeadzone) {
    dx = -1;
  } else if (state.gamepad.thumb_lx > kDeadzone) {
    dx = 1;
  }
  if (state.gamepad.thumb_ly > kDeadzone) {
    dy = -1;
  } else if (state.gamepad.thumb_ly < -kDeadzone) {
    dy = 1;
  }

  const auto now = std::chrono::steady_clock::now();
  if (dx != 0 || dy != 0) {
    if (dx != last_dx_ || dy != last_dy_ || now >= next_repeat_) {
      if (dy < 0) {
        press_key(ImGuiKey_UpArrow);
      } else if (dy > 0) {
        press_key(ImGuiKey_DownArrow);
      }
      if (dx < 0) {
        press_key(ImGuiKey_LeftArrow);
      } else if (dx > 0) {
        press_key(ImGuiKey_RightArrow);
      }
      const bool changed = dx != last_dx_ || dy != last_dy_;
      next_repeat_ = now + std::chrono::milliseconds(changed ? 350 : 90);
      last_dx_ = dx;
      last_dy_ = dy;
    }
  } else {
    last_dx_ = 0;
    last_dy_ = 0;
    next_repeat_ = now;
  }
  previous_buttons_ = buttons;
#else
  (void)io;
#endif
}

void Pes6SettingsDialog::OnDraw(ImGuiIO& io) {
  DrawController(io);

  ImGui::SetNextWindowSize(ImVec2(720, 560), ImGuiCond_FirstUseEver);
  ImGui::SetNextWindowBgAlpha(0.92f);
  if (!ImGui::Begin("PES6 SYSTEM SETTINGS##pes6", &open_,
                    ImGuiWindowFlags_NoCollapse)) {
    ImGui::End();
    if (!open_) {
      Close();
    }
    return;
  }

  ImGui::TextWrapped(
      "Native recompilation settings. F5 closes this overlay; F4 exposes the "
      "complete cvar registry.");
  DrawPresets();

  if (ImGui::BeginTabBar("##pes6_settings_tabs")) {
    if (ImGui::BeginTabItem("Video")) {
      DrawVideo();
      ImGui::EndTabItem();
    }
    if (ImGui::BeginTabItem("Performance")) {
      DrawPerformance();
      ImGui::EndTabItem();
    }
    if (ImGui::BeginTabItem("Input")) {
      DrawInput();
      ImGui::EndTabItem();
    }
    if (ImGui::BeginTabItem("About")) {
      DrawAbout();
      ImGui::EndTabItem();
    }
    ImGui::EndTabBar();
  }

  ImGui::Separator();
  if (ImGui::Button("Save settings", ImVec2(160, 0))) {
    rex::cvar::SaveConfig(config_path_);
    saved_ = true;
  }
  ImGui::SameLine();
  if (saved_) {
    ImGui::TextColored(ImVec4(0.45f, 0.85f, 0.45f, 1.0f),
                       "Saved to %s", config_path_.filename().string().c_str());
  }
  ImGui::SameLine();
  ImGui::TextDisabled(
      "Changes marked [restart] apply after relaunch.");

  ImGui::End();

  if (!open_) {
    Close();
  }
}

}  // namespace pes6
