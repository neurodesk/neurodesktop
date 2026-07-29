# Issue tracker: GitHub

Issues, planning maps, and PRDs for this repository live in GitHub Issues in
`neurodesk/neurodesktop`. Use the `gh` CLI for normal issue operations and the
GitHub REST API through `gh api` for native sub-issue and dependency
relationships.

## Conventions

- Create an issue with `gh issue create --repo neurodesk/neurodesktop`.
- Read an issue and its comments with
  `gh issue view <number> --repo neurodesk/neurodesktop --comments`.
- List issues with `gh issue list --repo neurodesk/neurodesktop`, selecting
  only the states and labels relevant to the task.
- Comment with `gh issue comment <number> --repo neurodesk/neurodesktop`.
- Add or remove labels with `gh issue edit`.
- Close an issue with `gh issue close <number> --repo neurodesk/neurodesktop`.
- Refer to issues by linked title in human-readable text. Do not use a bare
  issue number as the issue's name.

When a skill says to publish to the issue tracker, create a GitHub issue.

## Wayfinding operations

Wayfinder maps use the `wayfinder:map` label. Their decision tickets use one
of `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or
`wayfinder:task`.

### Create a map and its children

Create the map first, then create each ticket as a separate issue. After every
issue has an id, add each ticket to the map with GitHub's native sub-issue
relationship:

```bash
sub_issue_id=$(gh api \
  repos/neurodesk/neurodesktop/issues/<ticket-number> \
  --jq .id)
gh api --method POST \
  repos/neurodesk/neurodesktop/issues/<map-number>/sub_issues \
  -F sub_issue_id="$sub_issue_id"
```

List a map's children with:

```bash
gh api --paginate \
  repos/neurodesk/neurodesktop/issues/<map-number>/sub_issues
```

### Wire blocking relationships

Create tickets before wiring dependencies. To record that one ticket is
blocked by another, obtain the blocking issue's REST id and add it to the
blocked ticket:

```bash
blocking_issue_id=$(gh api \
  repos/neurodesk/neurodesktop/issues/<blocking-ticket-number> \
  --jq .id)
gh api --method POST \
  repos/neurodesk/neurodesktop/issues/<blocked-ticket-number>/dependencies/blocked_by \
  -F issue_id="$blocking_issue_id"
```

List what blocks a ticket with:

```bash
gh api --paginate \
  repos/neurodesk/neurodesktop/issues/<ticket-number>/dependencies/blocked_by
```

### Find and claim the frontier

The frontier is the map's open, unassigned children for which the
`dependencies/blocked_by` response contains no open issue. Preserve the order
returned by the map's sub-issue endpoint.

Claim a frontier ticket before doing any work:

```bash
gh issue edit <ticket-number> \
  --repo neurodesk/neurodesktop \
  --add-assignee @me
```

Re-read the issue after claiming it. If another assignee won the race, leave
that ticket alone and choose the next frontier ticket.

### Resolve a ticket

Post the answer as a resolution comment, close the ticket, then append one
linked one-line gist to the map's `Decisions so far` section. Put detailed
reasoning and asset links in the ticket, not in the map.

Use the `2026-03-10` GitHub REST API version when sending an explicit
`X-GitHub-Api-Version` header.
