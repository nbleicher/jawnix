#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=${1:-"$ROOT/.scraper-src"}
SCRAPER_REPO=${SCRAPER_REPO:-git@github.com:nbleicher/scraper.git}
SCRAPER_REVISION=${SCRAPER_REVISION:-7caca2ce8122c0ffaf47ca5737a06d05a23a90ca}
SCRAPER_DOCKERFILE_BLOB=${SCRAPER_DOCKERFILE_BLOB:-d7dc0cf3e8bb45abb46643edbb0efb6e53b9594f}

source_is_exact() {
    [ -d "$TARGET/.git" ] || return 1
    [ "$(git -C "$TARGET" rev-parse HEAD)" = "$SCRAPER_REVISION" ] \
        || return 1
    status=$(
        git -C "$TARGET" status --porcelain \
            --untracked-files=all \
            --ignored=matching
    )
    [ "$status" = " M Dockerfile.saas" ] || return 1
    [ "$(git -C "$TARGET" hash-object Dockerfile.saas)" = "$SCRAPER_DOCKERFILE_BLOB" ]
}

if [ -d "$TARGET/.git" ]; then
    current=$(git -C "$TARGET" rev-parse HEAD)
    if source_is_exact; then
        echo "Scraper source already pinned at $SCRAPER_REVISION."
        exit 0
    fi
    echo "Existing scraper checkout does not match the exact pinned revision and patch set (HEAD $current): $TARGET" >&2
    exit 1
fi

if [ -e "$TARGET" ]; then
    echo "Refusing to replace an existing non-checkout path: $TARGET" >&2
    exit 1
fi

git clone --filter=blob:none --no-checkout "$SCRAPER_REPO" "$TARGET"
git -C "$TARGET" fetch --depth=1 origin "$SCRAPER_REVISION"
git -C "$TARGET" checkout --detach "$SCRAPER_REVISION"
git -C "$TARGET" apply "$ROOT/scraper_changes/worker-build.patch"
if ! source_is_exact; then
    echo "Fetched scraper source does not match the pinned patch set." >&2
    exit 1
fi

for required in go.mod Dockerfile.saas migrations; do
    if [ ! -e "$TARGET/$required" ]; then
        echo "Pinned scraper source is missing $required." >&2
        exit 1
    fi
done

echo "Scraper source pinned at $SCRAPER_REVISION in $TARGET."
