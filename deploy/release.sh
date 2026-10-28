#!/bin/sh
# Build the deployment image: code from a git ref, data from this checkout. Run from the repo root, in Git Bash.
#
#   sh deploy/release.sh --ref submission          after the deadline: the submitted code, today's data
#   sh deploy/release.sh                           before it: code as it is in this folder (committed or not)
#   sh deploy/release.sh --ref submission --push   also push to PENN_REGISTRY (log Docker in to it first)
#   --no-export                                    reuse the dumps already in dumps/
#
# Data = the dumps (re-exported from your local ClickHouse) and data/, which carries the visitphilly snapshots
# made from pages saved by hand. Nothing is committed. The image is tagged <ref>-data-<YYYYMMDD-HHMM>.
set -eu
REF=worktree
PUSH=0
EXPORT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF=$2; shift 2 ;;
    --push) PUSH=1; shift ;;
    --no-export) EXPORT=0; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

TAG="$(echo "$REF" | tr -c 'A-Za-z0-9_.\n-' '-')-data-$(date +%Y%m%d-%H%M)"
CTX=.release
echo "== Code from: $REF   Image: penn-endpoint:$TAG"
[ "$REF" = worktree ] && echo "   (not a tag: after submission, use --ref submission so only data changes)"

[ "$EXPORT" = 1 ] && sh deploy/export_db.sh

rm -rf "$CTX" && mkdir -p "$CTX"
if [ "$REF" = worktree ]; then
  git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf - | tar -xf - -C "$CTX"
else
  git rev-parse --verify --quiet "$REF^{commit}" > /dev/null || { echo "no such git ref: $REF" >&2; exit 1; }
  git archive "$REF" | tar -xf - -C "$CTX"
fi
# Data is never taken from git: the dumps and data/ (which holds files git ignores) come from this folder.
rm -rf "$CTX/data" && cp -r data dumps "$CTX/"

docker build -f "$CTX/deploy/endpoint.Dockerfile" -t "penn-endpoint:$TAG" -t penn-endpoint:latest "$CTX"
rm -rf "$CTX"

# Each image is ~3 GB on disk: keep only this release locally (the registry keeps every dated one), and drop
# build cache older than a week.
docker images penn-endpoint --format '{{.Tag}}' | grep -- '-data-' | grep -vx "$TAG" | while read -r old; do
  docker rmi "penn-endpoint:$old" > /dev/null 2>&1 && echo "   removed old local image penn-endpoint:$old" \
    || echo "   kept penn-endpoint:$old (a container still uses it)"
done
docker builder prune -f --filter until=168h > /dev/null

if [ "$PUSH" = 1 ]; then
  : "${PENN_REGISTRY:?set PENN_REGISTRY, e.g. <account>.dkr.ecr.us-east-1.amazonaws.com}"
  # The deployment runs penn:live. Pushing a new image under that same tag and restarting the container keeps
  # its URL; the dated tag is the record of what was deployed when.
  docker tag "penn-endpoint:$TAG" "$PENN_REGISTRY/penn:$TAG"
  docker tag "penn-endpoint:$TAG" "$PENN_REGISTRY/penn:live"
  docker push "$PENN_REGISTRY/penn:$TAG"
  docker push "$PENN_REGISTRY/penn:live"
  echo "== Pushed $PENN_REGISTRY/penn:$TAG as penn:live. Restart the container to run it."
fi
echo "== Built penn-endpoint:$TAG"
