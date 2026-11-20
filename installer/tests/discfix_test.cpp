// Host tests for the disc image repair (hidden root directory). Build and run: tests/run.sh
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#include "discfix.h"
#include "fsx.h"

namespace fs = std::filesystem;
using Bytes = std::vector<std::uint8_t>;

namespace {

struct Layout { long size, head; };
const Layout kLayouts[] = {{2352, 24}, {2352, 16}, {2048, 0}};

void put32(Bytes& b, std::size_t at, std::uint32_t v) {
    for (int i = 0; i < 4; ++i) b[at + i] = static_cast<std::uint8_t>(v >> (8 * i));
}

Bytes record(const std::string& name, std::uint32_t lba, std::uint8_t flags) {
    std::size_t n = 33 + name.size() + (name.size() % 2 ? 0 : 1);
    Bytes r(n, 0);
    r[0] = static_cast<std::uint8_t>(n);
    put32(r, 2, lba);
    put32(r, 10, 2048);
    r[25] = flags;
    r[32] = static_cast<std::uint8_t>(name.size());
    std::memcpy(&r[33], name.data(), name.size());
    return r;
}

/// An image with the volume descriptor at sector 16 and the root directory at sector 22.
Bytes image(Layout l, const std::vector<std::pair<std::string, std::uint8_t>>& entries) {
    Bytes img(static_cast<std::size_t>(l.size) * 40, 0);
    std::size_t pvd = 16 * l.size + l.head;
    img[pvd] = 1;
    std::memcpy(&img[pvd + 1], "CD001", 5);
    img[pvd + 156] = 34;
    put32(img, pvd + 158, 22);
    put32(img, pvd + 166, 2048);
    img[pvd + 181] = 2;
    std::size_t at = 22 * l.size + l.head;
    std::uint32_t lba = 30;
    for (const auto& [name, flags] : entries) {
        Bytes r = record(name, lba++, flags);
        std::memcpy(&img[at], r.data(), r.size());
        at += r.size();
    }
    return img;
}

std::vector<int> flags(const Bytes& img, Layout l) {
    std::vector<int> out;
    std::size_t at = 22 * l.size + l.head, end = at + 2048;
    while (at < end && img[at]) {
        out.push_back(img[at + 25]);
        at += img[at];
    }
    return out;
}

struct Dir {
    fs::path root = fs::temp_directory_path() / ("discfix_" + std::to_string(std::rand()));
    Dir() { fs::create_directories(root); }
    ~Dir() { fs::remove_all(root); }
    std::string path(const std::string& n) const { return (root / n).string(); }
};

const std::vector<std::pair<std::string, std::uint8_t>> kHiddenRoot = {
    {std::string(1, '\0'), 3}, {std::string(1, '\1'), 3}, {"ANA", 3}, {"SYSTEM.CNF;1", 1}, {"SLUS_010.13;1", 1}};

void testHiddenBitIsClearedAndOtherBitsStay() {
    for (Layout l : kLayouts) {
        Dir d;
        assert(fsx::writeBytes(d.path("g.bin"), image(l, kHiddenRoot)));
        assert(discfix::unhideRoot(d.path("g.bin")) == 5);
        Bytes got;
        assert(fsx::readBytes(d.path("g.bin"), got));
        assert((flags(got, l) == std::vector<int>{2, 2, 2, 0, 0}));
    }
}

void testOnlyTheRootSectorChanges() {
    for (Layout l : kLayouts) {
        Dir d;
        Bytes before = image(l, kHiddenRoot);
        assert(fsx::writeBytes(d.path("g.bin"), before));
        discfix::unhideRoot(d.path("g.bin"));
        Bytes after;
        assert(fsx::readBytes(d.path("g.bin"), after));
        assert(after.size() == before.size());
        for (std::size_t i = 0; i < before.size(); ++i)
            if (before[i] != after[i]) assert(i / l.size == 22);
    }
}

void testNothingHiddenIsLeftUntouched() {
    Dir d;
    Bytes before = image(kLayouts[0], {{"SYSTEM.CNF;1", 0}, {"PSX", 2}});
    assert(fsx::writeBytes(d.path("g.bin"), before));
    assert(discfix::unhideRoot(d.path("g.bin")) == 0);
    Bytes after;
    assert(fsx::readBytes(d.path("g.bin"), after) && after == before);
}

void testNotAnIsoAndMissingFilesAreRefused() {
    Dir d;
    assert(fsx::writeBytes(d.path("x.bin"), Bytes(100000, 7)));
    assert(discfix::unhideRoot(d.path("x.bin")) == -1);
    assert(discfix::unhideRoot(d.path("missing.bin")) == -1);
}

void testFixDirectoryOnlyTouchesDiscImagesThatNeedIt() {
    Dir d;
    assert(fsx::writeBytes(d.path("Legend.bin"), image(kLayouts[0], kHiddenRoot)));
    assert(fsx::writeBytes(d.path("Fine.BIN"), image(kLayouts[0], {{"SYSTEM.CNF;1", 0}})));
    assert(fsx::writeBytes(d.path("Cooked.iso"), image(kLayouts[2], kHiddenRoot)));
    assert(fsx::writeBytes(d.path("Legend.cue"), Bytes{'F', 'I', 'L', 'E'}));
    assert(fsx::writeBytes(d.path("Legend.png"), Bytes(10, 1)));
    const auto done = discfix::fixDirectory(d.root.string());
    assert((done == std::vector<std::string>{"Cooked.iso", "Legend.bin"}));
    assert(discfix::fixDirectory(d.root.string()).empty());  // a second run does nothing
    assert(discfix::fixDirectory(d.path("nothing")).empty());
}

}  // namespace

int main() {
    testHiddenBitIsClearedAndOtherBitsStay();
    testOnlyTheRootSectorChanges();
    testNothingHiddenIsLeftUntouched();
    testNotAnIsoAndMissingFilesAreRefused();
    testFixDirectoryOnlyTouchesDiscImagesThatNeedIt();
    std::puts("all discfix tests passed");
    return 0;
}
