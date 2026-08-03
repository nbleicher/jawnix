#!/usr/bin/env sh
set -eu

fail() {
  echo "$1" >&2
  exit 2
}

if [ "$#" -ne 1 ]; then
  fail "usage: JAWNIX_DEPLOY_TARGET=user@host ops/deploy.sh TAG"
fi

: "${JAWNIX_DEPLOY_TARGET:?set JAWNIX_DEPLOY_TARGET to the application host}"

command -v git >/dev/null 2>&1 || fail "git is required."
command -v rsync >/dev/null 2>&1 || fail "rsync is required."
command -v tar >/dev/null 2>&1 || fail "tar is required."

release_tag="$1"
app_dir="${JAWNIX_DEPLOY_APP_DIR:-/srv/jawnix/app}"
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" ||
  fail "Run this script from a Git checkout."
cd "$repo_root"

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  fail "Refusing to deploy from a dirty checkout."
fi

git check-ref-format "refs/tags/$release_tag" >/dev/null 2>&1 ||
  fail "Invalid release tag: $release_tag"
git fetch --quiet origin main --tags
git ls-remote --exit-code --tags origin "refs/tags/$release_tag" >/dev/null 2>&1 ||
  fail "Release tag does not exist on origin: $release_tag"

release_commit="$(git rev-parse --verify "refs/tags/$release_tag^{commit}" 2>/dev/null)" ||
  fail "Release tag does not resolve to a commit: $release_tag"
head_commit="$(git rev-parse --verify HEAD^{commit})"
main_commit="$(git rev-parse --verify FETCH_HEAD^{commit})"

if [ "$head_commit" != "$release_commit" ]; then
  fail "Refusing to deploy: HEAD is not the requested release tag $release_tag."
fi

if ! git merge-base --is-ancestor "$release_commit" "$main_commit"; then
  fail "Refusing to deploy: $release_tag is not a revision on origin/main."
fi

deploy_source="$(mktemp -d "${TMPDIR:-/tmp}/jawnix-deploy.XXXXXX")"
trap 'rm -rf "$deploy_source"' EXIT HUP INT TERM
git archive "$release_commit" | tar -x -C "$deploy_source"

run_rsync() {
  rsync \
    --archive \
    --delete \
    --itemize-changes \
    --human-readable \
    --exclude='/.git' \
    --exclude='/.env' \
    --exclude='/config.js' \
    --exclude='/.venv/' \
    --exclude='/jawnix_vps.egg-info/' \
    --exclude='/jawnix-dev.db' \
    --exclude='/batches/' \
    --exclude='/backups/' \
    --exclude='/invoices/' \
    --exclude='/monitoring/' \
    --exclude='/restic-repository/' \
    --exclude='/migration/' \
    --exclude='/user-account-migration/' \
    "$@" \
    -- \
    "$deploy_source/" \
    "${JAWNIX_DEPLOY_TARGET}:${app_dir}/"
}

echo "Dry-run changes for $release_tag ($release_commit):"
echo "  destination: ${JAWNIX_DEPLOY_TARGET}:${app_dir}/"
run_rsync --dry-run

expected_confirmation="deploy $release_tag"
printf "Type '%s' to apply exactly the changes listed above: " "$expected_confirmation"
IFS= read -r confirmation || fail "Deployment cancelled."
if [ "$confirmation" != "$expected_confirmation" ]; then
  fail "Deployment cancelled."
fi

run_rsync
echo "Deployed $release_tag ($release_commit) to ${JAWNIX_DEPLOY_TARGET}:${app_dir}/"
