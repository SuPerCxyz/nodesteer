#!/bin/sh
set -eu

output=$1
hub_binary=$2
shift 2

test "$#" -ge 2
tmp="$output.tmp.$$"
trap 'rm -f "$tmp"' EXIT HUP INT TERM

cat "$hub_binary" >"$tmp"
printf '\nCADENTRA_AGENT_BUNDLE_V1\n' >>"$tmp"
while [ "$#" -gt 0 ]; do
	architecture=$1
	agent_binary=$2
	shift 2
	size=$(wc -c <"$agent_binary" | tr -d '[:space:]')
	printf '%s %s\n' "$architecture" "$size" >>"$tmp"
	cat "$agent_binary" >>"$tmp"
done

chmod 0755 "$tmp"
mv "$tmp" "$output"
trap - EXIT HUP INT TERM
