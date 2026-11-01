#!/bin/bash
# Size-based rotation for the wrapper scripts' own append-only logs.
#
# Sourced by run_tick.sh and run_incremental.sh. Nothing rotated these before,
# and the tick appends every drain's stdout to one file 288 times a day: by
# 2026-09-03 tick.log had reached 144 MB, which is the point at which the log
# stops being readable and so stops being read. A 5-day ingestion outage sat in
# it undetected.
#
# Rotation is **copy-truncate**, not rename. A drain started by a previous tick
# can still hold the log open, and a rename would leave it writing into the
# archived inode while the live file stayed empty — the failure mode where
# rotation silently costs you the logs it was meant to preserve. Truncating in
# place keeps every existing descriptor valid.
#
# The cost of copy-truncate is that anything written between the copy and the
# truncate is lost. Rotation runs before this tick does any work, but scan and
# drain share tick.log and a drain from another tick can be writing through it, so
# that window is the time gzip takes on 20 MiB. It is small and accepted, and it
# cannot be closed without giving up what copy-truncate is here for.
#
# Two wrappers can reach this at once, so rotation takes a lock. Without one, both
# copy, and the second can truncate what the first has not read. The lock is a
# directory because mkdir is atomic and macOS ships no flock(1). Losing the race
# means skipping: the file is still oversized on the next tick. A killed run can
# leave the lock behind, so one older than any plausible rotation is cleared
# rather than stopping rotation for good.

# rotate_log <path> [max_bytes] [keep]
rotate_log() {
    local path="$1"
    local max_bytes="${2:-${LOG_MAX_BYTES:-20971520}}"   # 20 MiB
    local keep="${3:-${LOG_KEEP:-5}}"

    [[ -f "$path" ]] || return 0

    # stat(1) is BSD here; -f%z is the size. Guard against it failing rather
    # than letting an empty string reach the comparison.
    local size
    size="$(stat -f%z "$path" 2>/dev/null)" || return 0
    [[ -n "$size" ]] || return 0
    (( size > max_bytes )) || return 0

    local lock="${path}.rotate.lock"
    if [[ -n "$(find "$lock" -maxdepth 0 -mmin +10 2>/dev/null)" ]]; then
        rmdir "$lock" 2>/dev/null
    fi
    mkdir "$lock" 2>/dev/null || return 0

    # Re-read under the lock: the holder before us may have just rotated this.
    size="$(stat -f%z "$path" 2>/dev/null)"
    if [[ -z "$size" ]] || (( size <= max_bytes )); then
        rmdir "$lock" 2>/dev/null
        return 0
    fi

    # The name only becomes an archive once it is complete, so a failure or a kill
    # never leaves a partial .gz for the pruning below to count.
    local archive="${path%.log}.$(date +%Y-%m-%d_%H-%M-%S).log.gz"
    local partial="${archive}.partial"
    if gzip -c "$path" > "$partial" 2>/dev/null && mv "$partial" "$archive"; then
        : > "$path"
        echo "=== $(date) rotated $(basename "$path") (${size} bytes) -> $(basename "$archive") ===" >> "$path"
    else
        # A failed rotation must not take the run with it: the log stays as it
        # is and the next tick tries again.
        rm -f "$partial"
        rmdir "$lock" 2>/dev/null
        return 0
    fi

    # Prune oldest archives. The timestamp is in the name, so lexical order is
    # chronological order.
    local prefix="${path%.log}"
    local -a archives
    local f
    while IFS= read -r f; do archives+=("$f"); done < <(
        ls -1 "${prefix}".*.log.gz 2>/dev/null | sort
    )
    local excess=$(( ${#archives[@]} - keep ))
    if (( excess > 0 )); then
        local i
        for (( i = 0; i < excess; i++ )); do
            rm -f "${archives[$i]}"
        done
    fi

    rmdir "$lock" 2>/dev/null
    return 0
}
