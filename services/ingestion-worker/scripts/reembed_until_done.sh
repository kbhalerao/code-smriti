#!/bin/bash
# Re-embed the corpus, resuming until the manifest exists.
#
# The re-embed has twice been terminated by a signal partway through — no
# traceback, no crash report, no memory pressure, only a resource_tracker
# warning at shutdown. Cause undetermined. Rather than diagnose it further, this
# resumes from the last logged cursor until the run writes its manifest, which it
# does only on a clean finish.
#
# Work is selected by document_id ordering, so resuming re-embeds nothing twice
# and skips nothing.
set -u
ROOT=/Users/kaustubh/code/code-smriti/services/ingestion-worker
cd "$ROOT" || exit 1
PY=$ROOT/.venv/bin/python
LOG=$ROOT/logs/reembed_corpus.log
MAX=20

for i in $(seq 1 $MAX); do
    if grep -q "wrote embedding_manifest" "$LOG" 2>/dev/null; then
        echo "$(date '+%F %T') manifest present — done after $((i-1)) attempt(s)"
        exit 0
    fi
    CURSOR=$(grep -oE "cursor [0-9a-f]+" "$LOG" 2>/dev/null | tail -1 | awk '{print $2}')
    if [ -n "$CURSOR" ]; then
        echo "$(date '+%F %T') attempt $i, resuming after $CURSOR"
        "$PY" "$ROOT/scripts/reembed_corpus.py" --execute --after "$CURSOR" >> "$LOG" 2>&1
    else
        echo "$(date '+%F %T') attempt $i, starting from the beginning"
        "$PY" "$ROOT/scripts/reembed_corpus.py" --execute >> "$LOG" 2>&1
    fi
    sleep 5
done
echo "$(date '+%F %T') gave up after $MAX attempts"
exit 1
