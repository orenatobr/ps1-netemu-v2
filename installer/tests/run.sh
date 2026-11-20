#!/bin/sh
# Builds and runs the host tests of the installer logic.
set -eu
cd "$(dirname "$0")/.."
out=$(mktemp -d)
c++ -std=c++20 -Wall -Wextra -Isource tests/installer_test.cpp source/installer.cpp source/fsx.cpp -o "$out/t"
"$out/t"
c++ -std=c++20 -Wall -Wextra -Isource tests/discfix_test.cpp source/discfix.cpp source/fsx.cpp -o "$out/d"
"$out/d"
rm -rf "$out"
