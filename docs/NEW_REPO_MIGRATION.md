# New Repository Migration

The project is being renamed to **Discovery Co-Scientist** and should live in a
new repository named `discovery-co-scientist`.

## Why a New Repository

The current branch substantially extends the original open-source project. It
adds a Robin-inspired therapeutic discovery workflow, new data models, new
agents, new prompts, new analysis artifacts, workflow selection, and updated
public branding. Those changes make the project more than a small patch series
against the original repository.

## Compatibility

- New distribution name: `discovery-co-scientist`.
- New primary CLI: `discovery-coscientist`.
- Compatibility alias retained for one release: `co-scientist`.
- Internal Python imports remain `co_scientist` for this implementation slice.

## Local Migration

Existing local config files can continue to use the same provider and model
settings. Existing session databases without the new `workflow` column are
migrated by `0006_robin_workflow.sql`, which defaults prior sessions to
`general_hypothesis`.

Recommended migration commands:

```bash
git remote rename origin upstream
git remote add origin git@github.com:<owner>/discovery-co-scientist.git
discovery-coscientist init
```

The GitHub repository creation itself should be performed once the owner,
visibility, and remote URL are confirmed.
