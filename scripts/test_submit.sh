#!/bin/bash
set -e
BASE_URL="${1:-http://localhost:8000}"

echo "=== Submitting test job to $BASE_URL ==="
JOB=$(curl -s -X POST "$BASE_URL/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "LargeDataset",
    "brief_content": "project_name: test_job\noutput_dir: ./output",
    "job_name": "API smoke test"
  }')
echo "Response: $JOB"

JOB_ID=$(echo $JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Job ID: $JOB_ID"

echo ""
echo "=== Polling status ==="
for i in $(seq 1 15); do
  RESP=$(curl -s "$BASE_URL/jobs/$JOB_ID")
  STATUS=$(echo $RESP | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "  [$i] status: $STATUS"
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  sleep 2
done

echo ""
echo "=== Job logs ==="
curl -s "$BASE_URL/jobs/$JOB_ID/logs"

echo ""
echo "=== All jobs ==="
curl -s "$BASE_URL/jobs" | python3 -m json.tool
