#!/bin/bash
# apt-get for the fork's CI runners: Azure's Ubuntu mirror first, a time limit per call and one
# retry on the main archive (a stalled or unreachable mirror failed several runs), and a .deb
# cache keyed by the exact package versions (actions/cache restores / saves ~/apt-cache).
#   .github/ci/apt.sh --mirrors              the runner's mirror list -> Azure first, archive second
#   .github/ci/apt.sh key <name> <pkgs...>   key=<name>-<release>-<hash of the .deb names> into
#                                            $GITHUB_OUTPUT (empty: nothing to download)
#   .github/ci/apt.sh install-cached <pkgs>  install through ~/apt-cache, no recommends
#   .github/ci/apt.sh <apt-get args>         plain apt-get with the time limit and the retry
set -u
A=http://azure.archive.ubuntu.com/ubuntu/
B=http://archive.ubuntu.com/ubuntu/
LIST=/etc/apt/apt-mirrors.txt   # what the runner's ubuntu.sources point at (mirror+file:)
CACHE=$HOME/apt-cache
mirrors() { printf '%s\tpriority:1\n%s\tpriority:2\n' "$1" "$2" | sudo tee "$LIST" > /dev/null; }
run() { timeout 300 sudo apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 "$@"; }
retry() {
    run "$@" && return 0
    first=$(head -1 "$LIST" 2>/dev/null | cut -f1)
    echo "::warning::apt-get $1 failed or stalled (mirror ${first:-?}): once more on the other mirror"
    if [ "$first" = "$A" ]; then mirrors "$B" "$A"; else mirrors "$A" "$B"; fi
    sleep 5
    run "$@"
}
# Sandbox user root: apt's _apt user cannot reach the runner's home (it downloads as root anyway).
OPTS=(-o Dir::Cache::Archives="$CACHE" -o APT::Keep-Downloaded-Packages=true
      -o APT::Sandbox::User=root --no-install-recommends)

case "${1:-}" in
--mirrors)
    grep -rq "mirror+file:$LIST" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null ||
        echo "::warning::the apt sources do not use $LIST: mirror choice has no effect"
    mirrors "$A" "$B"
    ;;
key)
    name=$2; shift 2
    mkdir -p "$CACHE/partial"
    debs=$(sudo apt-get install --print-uris -qq "${OPTS[@]}" "$@" | cut -d"'" -f2 | xargs -r -n1 basename | sort)
    echo "$debs"
    key=""
    [ -n "$debs" ] && key="$name-$(lsb_release -rs)-$(echo "$debs" | sha256sum | cut -c1-16)"
    echo "key=$key" >> "${GITHUB_OUTPUT:-/dev/null}"
    echo "cache key: ${key:-none (nothing to download)}"
    ;;
install-cached)
    shift
    mkdir -p "$CACHE/partial"
    echo "to download: $(sudo apt-get install --print-uris -qq "${OPTS[@]}" "$@" | wc -l) package(s)"
    retry install -y -q "${OPTS[@]}" "$@" || exit 1
    sudo chown -R "$(id -u):$(id -g)" "$CACHE"   # apt leaves lock and partial/ root-only
    ls "$CACHE"/*.deb 2>/dev/null | wc -l | sed 's/^/cached .deb files: /'
    ;;
*)
    retry "$@"
    ;;
esac
