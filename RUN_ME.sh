#!/bin/bash
#
# ⚡ RUN THIS SCRIPT TO PROCESS THE QUEUE ⚡
#
# This is the CORRECT command to process all pending jobs.
#

echo "=================================================="
echo "⚡ MMM Queue Processor"
echo "=================================================="
echo ""
echo "Running the correct script: process_queue_simple.py"
echo ""

python scripts/process_queue_simple.py --loop

echo ""
echo "=================================================="
echo "✅ Done!"
echo "=================================================="
