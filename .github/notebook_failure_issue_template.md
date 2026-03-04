---
title: "Notebook Test Job failure - {{ env.GITHUB_WORKFLOW }}"
assignees:
  - stebo85
  - aswinnarayanan
  - akshitbeniwal
labels:
  - bug
---

## JupyterHub Notebook Testing Job Failure

**Server:** {{ env.SERVER_NAME }}
**Server URL:** {{ env.SERVER_URL }}
**Workflow:** {{ env.GITHUB_WORKFLOW }}
**Run ID:** {{ env.GITHUB_RUN_ID }}

### Failed Workflow Details
- **Workflow URL:** {{ env.GITHUB_SERVER_URL }}/{{ env.GITHUB_REPOSITORY }}/actions/runs/{{ env.GITHUB_RUN_ID }}
- **Repository:** {{ env.GITHUB_REPOSITORY }}
- **Server Name:** {{ env.SERVER_NAME }}
- **Server URL:** {{ env.SERVER_URL }}
- **Date:** {{ date | date('YYYY-MM-DD HH:mm:ss UTC') }}
- **Notebook Execution Status:** {{ env.NOTEBOOK_SUCCESS }}
- **Success Patterns Found:** {{ env.PATTERNS_FOUND }}/8

### Description

@aswinnarayanan @stebo85 @akshitbeniwal - The notebook execution test has failed for the **{{ env.SERVER_NAME }}**.

### Additional Info
- The failure occurred during automated testing of notebook functionality (FSL BET course)
- Please check notebook creation, papermill execution, and FSL functionality tests for specific failure points
- Expected success patterns: FSL module loading, course data download, BET execution, and completion markers