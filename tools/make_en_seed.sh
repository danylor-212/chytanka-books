#!/usr/bin/env bash
# Package the local English build cache as the CI seed and publish it as a release asset.
#   tools/make_en_seed.sh [tag]      (default: en-cache-<today>)
# Then set EN_SEED_TAG in .github/workflows/build.yml to the new tag.
# The tarball holds built/<se-repo>.epub + built/<se-repo>.sha (SE source commit) and pg/pg<N>.epub.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-en-cache-$(date +%F)}"
C="${CHYTANKA_CACHE:-.cache}/en"
OUT="$(mktemp -d)/en-cache.tar"
tar -cf "$OUT" -C "$C" built pg
sha256sum "$OUT"
gh release create "$TAG" "$OUT" --latest=false --title "English build cache $TAG" \
  --notes "CI seed for books-en.yaml: Standard Ebooks compatible EPUBs built with \`se build\` from github.com/standardebooks (CC0), keyed by source commit (*.sha), plus Project Gutenberg downloads. Not a user-facing release."
echo "published $TAG"
