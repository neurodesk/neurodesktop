---
title: "Job failure - {{ env.GITHUB_WORKFLOW }}"
assignees:
  - stebo85
  - aswinnarayanan
labels:
  - bug
---

## JupyterHub Testing Job Failure

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

### Description
@aswinnarayanan @stebo85 - The JupyterHub API testing job has failed for the **{{ env.SERVER_NAME }}** server. Please investigate the failure.


### Additional Information
- This issue was automatically created by the GitHub Actions workflow
- The failure occurred during automated testing of JupyterHub functionality
- Please check both basic terminal tests and FSL functionality tests for specific failure points