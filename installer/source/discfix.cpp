#include "discfix.h"

#include <cstdint>
#include <cstdio>

#include "fsx.h"

namespace discfix {

namespace {

constexpr int kHidden = 0x01;          // ISO9660 file flags, bit 0: existence (hidden)
constexpr long kPvdSector = 16;
constexpr std::size_t kData = 2048;
struct Layout { long size, head; };
constexpr Layout kLayouts[] = {{2352, 24}, {2352, 16}, {2048, 0}};  // raw mode 2, raw mode 1, cooked

struct File {
    std::FILE* f;
    explicit File(const std::string& p) : f(std::fopen(p.c_str(), "r+b")) {}
    ~File() { if (f) std::fclose(f); }
    File(const File&) = delete;
    File& operator=(const File&) = delete;
    bool at(long long off) { return f && fseeko(f, static_cast<off_t>(off), SEEK_SET) == 0; }
    bool read(void* p, std::size_t n) { return std::fread(p, 1, n, f) == n; }
    bool write(const void* p, std::size_t n) { return std::fwrite(p, 1, n, f) == n && std::fflush(f) == 0; }
};

std::uint32_t le32(const std::uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | (static_cast<std::uint32_t>(p[3]) << 24); }

bool endsWithNoCase(const std::string& s, const std::string& ext) {
    if (s.size() < ext.size()) return false;
    for (std::size_t i = 0; i < ext.size(); ++i) {
        char c = s[s.size() - ext.size() + i];
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
        if (c != ext[i]) return false;
    }
    return true;
}

}  // namespace

int unhideRoot(const std::string& path) {
    File file(path);
    if (!file.f) return -1;
    std::uint8_t pvd[190];
    const Layout* found = nullptr;
    for (const Layout& l : kLayouts) {
        if (file.at(kPvdSector * l.size + l.head) && file.read(pvd, sizeof pvd) && pvd[0] == 1 &&
            pvd[1] == 'C' && pvd[2] == 'D' && pvd[3] == '0' && pvd[4] == '0' && pvd[5] == '1') {
            found = &l;
            break;
        }
    }
    if (!found) return -1;
    const std::uint8_t* root = pvd + 156;  // the root directory record inside the volume descriptor
    const std::uint32_t lba = le32(root + 2), length = le32(root + 10);
    const std::uint32_t sectors = length ? (length + kData - 1) / kData : 1;
    int cleared = 0;
    for (std::uint32_t s = 0; s < sectors; ++s) {  // a directory is a run of whole sectors
        const long long off = static_cast<long long>(lba + s) * found->size + found->head;
        std::uint8_t data[kData];
        if (!file.at(off) || !file.read(data, kData)) return -1;
        int n = 0;
        for (std::size_t i = 0; i + 33 < kData && data[i];) {
            if (data[i + 25] & kHidden) {
                data[i + 25] = static_cast<std::uint8_t>(data[i + 25] & ~kHidden);
                ++n;
            }
            i += data[i];
        }
        if (!n) continue;
        std::uint8_t back[kData];
        if (!file.at(off) || !file.write(data, kData) || !file.at(off) || !file.read(back, kData)) return -1;
        for (std::size_t i = 0; i < kData; ++i)
            if (back[i] != data[i]) return -1;
        cleared += n;
    }
    return cleared;
}

std::vector<std::string> fixDirectory(const std::string& dir) {
    std::vector<std::string> changed;
    for (const std::string& name : fsx::listDir(dir)) {
        if (!(endsWithNoCase(name, ".bin") || endsWithNoCase(name, ".img") || endsWithNoCase(name, ".iso") ||
              endsWithNoCase(name, ".mdf")))
            continue;
        if (unhideRoot(dir + "/" + name) > 0) changed.push_back(name);
    }
    return changed;
}

}  // namespace discfix
