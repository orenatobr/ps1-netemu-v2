#include "ui.h"

#include <algorithm>
#include <string>

#ifndef APP_VERSION
#error "APP_VERSION must be set by the Makefile (the release version, read from the VERSION file)"
#endif

namespace ui {

const Text kEn = {"PS1 emulator patch v" APP_VERSION,
                  "ps1_netemu.self: DuckStation-style vertical overscan (NTSC), 4:3 width unchanged.",
                  "File on the console", "Original (not patched)", "Patched (v" APP_VERSION ")", "Unknown build", "Not found",
                  "Install the patch", "Restore the original", "Exit", "Triangle",
                  "Installed. The original is saved in /dev_hdd0/ps1_netemu_stock.self. Start a PS1 game.",
                  "Already installed.", "Original restored.",
                  "This file is not the build the patch was made for. Nothing was changed.",
                  "The package is damaged. Nothing was changed.", "No backup found. Nothing to restore.",
                  "Write failed. The original was kept; try again.", "Could not open the system files. This needs custom firmware.",
                  "The patch is installed but there is no backup of the original, so Triangle cannot restore it. Copy the original to /dev_hdd0/ps1_netemu_stock.self.",
                  "Installing the patch...", "Restoring the original...", "Do not turn off the console.",
                  {"Checking the package", "Reading the emulator file", "Checking the backup", "Writing the file",
                   "Verifying the new file", "Final check"}};

std::string message(const Text& t, const installer::Outcome& o) {
    if (o.code == "installed") return t.installed;
    if (o.code == "already") return t.already;
    if (o.code == "restored") return t.restored;
    if (o.code == "missing") return t.missing;
    if (o.code == "not_stock") return t.notStock;
    if (o.code == "bad_payload") return t.badPayload;
    if (o.code == "no_backup") return t.noBackup;
    return t.ioError;
}

namespace {

void header(gfx::Canvas& c, const Text& t) {
    using namespace gfx;
    c.clear(theme::bg);
    c.checkBadge(112, 112, 34, theme::greenDark, theme::text);
    c.text(t.title, Size::Title, theme::text, 170, 82);
    int sy = 170;
    for (const auto& line : c.wrap(t.subtitle, Size::Body, 1120)) {
        c.text(line, Size::Body, theme::muted, 80, sy);
        sy += c.lineHeight(Size::Body) + 2;
    }
}

}  // namespace

void draw(gfx::Canvas& c, const Text& t, installer::State state, bool mounted, const std::string& note, bool noteOk,
          bool circleConfirms) {
    using namespace gfx;
    header(c, t);
    c.card(80, 250, 1120, 130, 18, theme::card, 2, theme::line);
    c.text(t.file, Size::Small, theme::muted, 120, 276);
    const char* label = !mounted ? "-"
                        : state == installer::State::Patched ? t.patched
                        : state == installer::State::Stock   ? t.stock
                        : state == installer::State::Unknown ? t.unknown
                                                             : t.missing;
    const Color tone = state == installer::State::Patched ? theme::green
                       : state == installer::State::Stock ? theme::text
                                                          : theme::amber;
    c.text(label, Size::Large, mounted ? tone : theme::red, 120, 312, Align::Left, 1040);
    if (!note.empty()) {
        int y = 420;
        for (const auto& line : c.wrap(note, Size::Body, 1120)) {
            c.text(line, Size::Body, noteOk ? theme::green : theme::amber, 80, y);
            y += c.lineHeight(Size::Body) + 4;
        }
    }
    const std::string a = circleConfirms ? "O" : "X";
    const std::string b = circleConfirms ? "X" : "O";
    c.fill(0, 640, 1280, 80, 0, theme::card);
    c.text(a + "  " + t.install, Size::Body, theme::text, 80, 668);
    c.text(std::string(t.triangle) + "  " + t.restore, Size::Body, theme::text, 470, 668);
    c.text(b + "  " + t.exit, Size::Body, theme::text, 1000, 668);
}

void drawProgress(gfx::Canvas& c, const Text& t, bool installing, installer::Stage stage, float fraction) {
    using namespace gfx;
    header(c, t);
    const float f = fraction < 0 ? 0 : fraction > 1 ? 1 : fraction;
    const int barX = 120, barY = 346, barW = 1040, barH = 24;
    c.card(80, 250, 1120, 130, 18, theme::card, 2, theme::line);
    c.text(installing ? t.installing : t.restoring, Size::Small, theme::muted, 120, 276);
    c.text(t.stage[static_cast<int>(stage)], Size::Body, theme::text, 120, 304);
    c.fill(barX, barY, barW, barH, barH / 2, theme::raised);
    if (f > 0) c.fill(barX, barY, std::max(barH, static_cast<int>(barW * f)), barH, barH / 2, theme::green);
    c.text(std::to_string(static_cast<int>(f * 100)) + "%", Size::Large, theme::text, 1160, 276, Align::Right);
    c.text(t.keepOn, Size::Body, theme::amber, 80, 420);
}

}  // namespace ui
