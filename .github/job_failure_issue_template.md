---
title: Job failure - {{ env.GITHUB_WORKFLOW }} - {{ date | date('YYYY-MM-DD HH:mm:ss.SSS') }}
labels: bug
---
{{ env.GITHUB_SERVER_URL }}/{{ env.GITHUB_REPOSITORY }}/actions/runs/{{ env.GITHUB_RUN_ID }}
