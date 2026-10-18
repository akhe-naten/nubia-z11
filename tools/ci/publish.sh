#!/usr/bin/env bash
# Publish the current state to the "dist" orphan branch.
#
# The GitHub artifact/log API is unreachable from the workstation, so this
# branch is the only channel back: Image.gz + build.log + META.txt.
#
#   publish.sh <phase> <result>
#
# Idempotent: wipes and re-creates out/, so it works no matter how many times
# it is called inside one job (the previous "-b dist" reuse bug is gone).
set -eu

PHASE="$1"
RESULT="${2:-unknown}"
WS="${GITHUB_WORKSPACE:-$(pwd)}"
OUT="$WS/out"

rm -rf "$OUT"
mkdir -p "$OUT"

if [ -f "$WS/build.log" ]; then
	# keep the head (early errors) and the tail (the failure itself)
	head -c 200000 "$WS/build.log" > "$OUT/build.log"
	printf "\n\n...... (truncated) ......\n\n" >> "$OUT/build.log"
	tail -c 200000 "$WS/build.log" >> "$OUT/build.log"
else
	echo "(no build.log)" > "$OUT/build.log"
fi

if [ -f "$WS/k/arch/arm64/boot/Image.gz" ]; then
	cp "$WS/k/arch/arm64/boot/Image.gz" "$OUT/Image.gz"
fi

# 内核配置回传：设备侧出问题时可以在工作站上逐项核对
if [ -f "$WS/k/.config" ]; then
	cp "$WS/k/.config" "$OUT/kernel.config"
fi

printf "sha=%s\nphase=%s\nresult=%s\ndate=%s\n" \
	"${GITHUB_SHA:-local}" "$PHASE" "$RESULT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT/META.txt"
if [ -f "$WS/k/.config" ]; then
	# 版本串单独列出，一眼可查，不必在 8000 行配置里翻
	printf "uname_r=%s\nconfig_sha=%s\n" \
		"${KREL:-unknown}" "$(sha256sum "$WS/k/.config" | cut -c1-16)" >> "$OUT/META.txt"
fi

# GITHUB_TOKEN is NOT exported into step scripts by default: the calling step
# must pass env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
	echo "publish: no token in GITHUB_TOKEN/GH_TOKEN" >&2
	exit 1
fi

cd "$OUT"
git init -q
git config user.name "z11-ci"
git config user.email "z11-ci@users.noreply.github.com"
git checkout -q -B dist
git add -A
git commit -q -m "$PHASE ${GITHUB_SHA::7} $RESULT"
git remote add origin "https://x-access-token:${TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git push -q -f origin dist

echo "published: phase=$PHASE result=$RESULT"
