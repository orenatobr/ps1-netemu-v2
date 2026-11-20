// Disc image repair: clears the ISO9660 hidden flag of the root directory (Legend of Mana has it on every entry).
// The PS3 does not list hidden entries of a mounted disc, so webMAN mounts nothing and the emulator exits at once
// (error 80028F17). Pure file logic, tested on the host.
#pragma once

#include <string>
#include <vector>

namespace discfix {

/// Clears the hidden flag of every entry of the root directory of one image (.bin raw 2352 or cooked 2048 sectors).
/// Returns the number of entries cleared (0 when none was hidden), or -1 when the file is not an ISO9660 image or the
/// write failed (the image is then left as it was).
int unhideRoot(const std::string& path);

/// Applies unhideRoot() to every .bin, .img, .iso and .mdf of a folder. Returns the names that were changed.
std::vector<std::string> fixDirectory(const std::string& dir);

}  // namespace discfix
