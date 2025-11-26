#!/bin/bash

# Automated post-sleep evaluation script
# This runs after the 2-hour sleep completes

echo "==================================="
echo "POST-SLEEP EVALUATION"
echo "Wake time: $(date)"
echo "==================================="

cd /Users/kaustubh/Documents/code/code-smriti/4-consume/api-server

# 1. Check re-embedding status
echo ""
echo "1. Checking re-embedding status..."
tail -30 /tmp/reembed-mps-small-batch.log | grep -E "Batch|Updated|COMPLETE|ERROR"

# Check if re-embedding completed
if grep -q "RE-EMBEDDING COMPLETE" /tmp/reembed-mps-small-batch.log; then
    echo "✓ Re-embedding completed successfully!"
else
    echo "⚠ Re-embedding may still be running or incomplete"
    tmux list-sessions | grep reembed
fi

# 2. Run direct vector search evaluation
echo ""
echo "2. Running direct vector search evaluation..."
source venv/bin/activate
python3 direct_vector_eval.py 2>&1 | tee /tmp/post_sleep_eval_output.log

# 3. Display summary
echo ""
echo "==================================="
echo "EVALUATION COMPLETE"
echo "==================================="
echo ""
echo "Results saved to:"
echo "  - /tmp/direct_vector_eval_results.json"
echo "  - /tmp/post_sleep_eval_output.log"
echo ""

# Extract and display key metrics
if [ -f /tmp/direct_vector_eval_results.json ]; then
    echo "Quick Summary:"
    python3 << 'EOF'
import json
with open('/tmp/direct_vector_eval_results.json', 'r') as f:
    data = json.load(f)
    summary = data['summary']
    print(f"  Success Rate: {summary['success_rate']*100:.1f}%")
    print(f"  MRR: {summary['mrr']:.3f}")
    print(f"  Precision@10: {summary['precision@10']:.3f}")
    print(f"  Recall: {summary['recall']:.3f}")
EOF
fi

echo ""
echo "Done at: $(date)"
