#!/bin/bash
set -e
echo "Worker container started at $(date)" >> /workspace/job.log
echo "STUB: full r-env environment not yet installed" >> /workspace/job.log
echo "Workspace contents:" >> /workspace/job.log
ls /workspace >> /workspace/job.log
echo "claude-skills-v2 contents:" >> /workspace/job.log
ls /claude-skills >> /workspace/job.log
