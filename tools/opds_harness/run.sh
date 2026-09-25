#!/usr/bin/env bash
# Compile the firmware's OpdsParser for the host and run it on the generated feeds.
#   FIRMWARE_DIR=~/src/chytanka-main tools/opds_harness/run.sh [feed.xml ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
FW="${FIRMWARE_DIR:-$HOME/src/chytanka-main}"
OUT="${TMPDIR:-/tmp}/chytanka-opds-harness"
[ -f "$FW/lib/OpdsParser/OpdsParser.cpp" ] || { echo "firmware not found at $FW (set FIRMWARE_DIR)"; exit 2; }
mkdir -p "$OUT"
g++ -std=c++20 -O1 -Wall -Wextra \
  -I"$HERE/stubs" -I"$FW/lib/OpdsParser" -I"$FW/lib/XmlParserUtils" -I"$FW/src/util" -I"$FW/lib/Utf8" \
  "$HERE/main.cpp" "$FW/lib/OpdsParser/OpdsParser.cpp" "$FW/src/util/UrlUtils.cpp" "$FW/lib/Utf8/Utf8.cpp" -lexpat -o "$OUT/opds_harness"
g++ -std=c++20 -O1 -Wall -Wextra \
  -I"$HERE/stubs" -I"$FW/lib/OpdsParser" -I"$FW/lib/XmlParserUtils" -I"$FW/src/util" -I"$FW/lib/Utf8" \
  "$HERE/crawl.cpp" "$FW/lib/OpdsParser/OpdsParser.cpp" "$FW/src/util/UrlUtils.cpp" "$FW/lib/Utf8/Utf8.cpp" -lexpat -o "$OUT/opds_crawl"
if [ "${1:-}" = "--crawl" ]; then
  shift
  BASE_URL="${BASE_URL:-https://danylor-212.github.io/chytanka-books/}"
  exec "$OUT/opds_crawl" --server "${SERVER_URL:-${BASE_URL}opds/index.xml}" --base "$BASE_URL" --dir "${PUBLIC_DIR:-$REPO/public}" "$@"
fi
if [ $# -eq 0 ]; then set -- "$REPO"/public/opds/*.xml; fi
BASE_URL="${BASE_URL:-https://danylor-212.github.io/chytanka-books/}"
"$OUT/opds_harness" --chunk 512 --feed-url "${BASE_URL}opds/index.xml" "$@"
