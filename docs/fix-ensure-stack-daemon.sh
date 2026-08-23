#!/usr/bin/env bash
#
# fix-ensure-stack-daemon.sh
#
# Finishes the 2026-08-22 move of code-smriti out of ~/Documents.
#
# Everything else in that move was repaired in-place, but
# /Library/LaunchDaemons/com.codesmriti.ensure-stack.plist is root-owned and
# still points at the old path. It fires every 300s, fails, and — because it is
# the headless-boot path that brings colima and ollama up — the stack will not
# come back on its own after a reboot until this is fixed.
#
# The repo copy at scripts/com.codesmriti.ensure-stack.plist is already correct
# and identical in every other key, so this installs it over the top.
#
# Usage:
#   ./docs/fix-ensure-stack-daemon.sh --check    # report only, changes nothing
#   ./docs/fix-ensure-stack-daemon.sh            # apply (re-runs itself as root)
#
set -euo pipefail

REPO=/Users/kaustubh/code/code-smriti
SRC="$REPO/scripts/com.codesmriti.ensure-stack.plist"
DST=/Library/LaunchDaemons/com.codesmriti.ensure-stack.plist
STUB=/Users/kaustubh/Documents/code/code-smriti
LABEL=com.codesmriti.ensure-stack

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
info() { printf '  · %s\n' "$1"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

fail() { bad "$1"; exit 1; }

# --------------------------------------------------------------------------
head_ "Preconditions"

[[ -d "$REPO/.git" ]] || fail "repo not found at $REPO"
ok "repo present at $REPO"

[[ -f "$SRC" ]] || fail "source plist missing: $SRC"
ok "source plist present"

# The source must already be patched, or we would install the same bug back.
if grep -q "Documents/code/code-smriti" "$SRC"; then
    fail "source plist still references ~/Documents — patch it first"
fi
ok "source plist is free of old paths"

python3 - "$SRC" <<'PY' || fail "source plist is not well-formed XML"
import plistlib, sys
d = plistlib.load(open(sys.argv[1], 'rb'))
prog = d['ProgramArguments'][1]
import os
assert os.path.isfile(prog), f"ProgramArguments target does not exist: {prog}"
PY
ok "source plist parses and its target script exists"

if [[ -f "$DST" ]]; then
    if grep -q "Documents/code/code-smriti" "$DST"; then
        info "installed daemon STILL points at ~/Documents  <- this is what we fix"
    else
        ok "installed daemon already points at the new path"
        if [[ $CHECK_ONLY -eq 0 ]]; then
            info "nothing to install; will still verify and clean up the stub"
        fi
    fi
else
    info "no installed daemon at $DST (will install)"
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
    head_ "Check-only mode — nothing changed"
    echo "  Re-run without --check to apply."
    exit 0
fi

# --------------------------------------------------------------------------
# Root from here on. Re-exec so the user gets a single password prompt.
if [[ $EUID -ne 0 ]]; then
    head_ "Elevating"
    info "installing to $DST needs root"
    exec sudo -- "$0" "$@"
fi

head_ "Installing"

cp "$SRC" "$DST"
chown root:wheel "$DST"
chmod 644 "$DST"
ok "installed, root:wheel 644"

# CLAUDE.md: plutil -lint alone is not sufficient — it accepts "--" inside an
# XML comment, which is illegal XML. Validate with both parsers.
plutil -lint "$DST" >/dev/null || fail "plutil rejected the installed plist"
python3 -c "import plistlib,sys;plistlib.load(open(sys.argv[1],'rb'))" "$DST" \
    || fail "plistlib rejected the installed plist"
ok "validated by both plutil and plistlib"

head_ "Reloading the daemon"

launchctl unload "$DST" 2>/dev/null || true
launchctl load  "$DST"
ok "unloaded and reloaded"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    ok "$LABEL is loaded in the system domain"
else
    bad "$LABEL not visible in launchctl list — check manually"
fi

# --------------------------------------------------------------------------
head_ "Removing the stub the broken daemon recreated"

if [[ -e "$STUB" ]]; then
    # Guard hard. Only remove something that is clearly the empty stub.
    if [[ -L "$STUB" ]]; then
        bad "$STUB is a symlink — refusing to touch it"
    elif [[ -e "$STUB/.git" ]]; then
        bad "$STUB contains a .git — that is a real repo, refusing"
    elif [[ $(find "$STUB" | wc -l | tr -d ' ') -gt 12 ]]; then
        bad "$STUB has more than 12 entries — not the stub, refusing"
        find "$STUB" -maxdepth 2 | head -15
    else
        find "$STUB" -mindepth 1 | sed 's|^|      |'
        rm -rf "$STUB"
        ok "removed $STUB"
    fi
else
    ok "no stub present"
fi

# --------------------------------------------------------------------------
head_ "Verification"

sleep 2
ERRLOG="$REPO/logs/ensure_stack.daemon.err"
STACKLOG="$REPO/logs/ensure_stack.log"

python3 - "$DST" <<'PY'
import plistlib, sys
d = plistlib.load(open(sys.argv[1], 'rb'))
print(f"      prog   {d['ProgramArguments'][1]}")
print(f"      stdout {d.get('StandardOutPath')}")
print(f"      stderr {d.get('StandardErrorPath')}")
PY

if [[ -s "$ERRLOG" ]]; then
    info "recent stderr from the daemon:"
    tail -5 "$ERRLOG" | sed 's|^|      |'
else
    ok "daemon stderr is empty"
fi

if [[ -f "$STACKLOG" ]]; then
    info "recent ensure_stack.log:"
    tail -5 "$STACKLOG" | sed 's|^|      |'
fi

head_ "Done"
echo "  The daemon runs every 300s. If ensure_stack.log stays quiet, check again"
echo "  in five minutes:  tail -20 $STACKLOG"
echo
