---
name: Bug Report
about: Something is broken or behaving unexpectedly
labels: bug
assignees: ''
---

## Description

A clear description of what is broken.

## Steps to Reproduce

1.
2.
3.

## Expected Behavior

What should have happened.

## Actual Behavior

What actually happened. Include which component failed:

- [ ] InvestigationCapsule schema / validation
- [ ] LoopCheckpoint schema / validation
- [ ] WorkerHandoff schema / validation
- [ ] ContextGuard enforcement (which hook)
- [ ] Claude Code adapter
- [ ] Other adapter
- [ ] `.context/` layout / git policy

## Environment

- Runtime / agent framework:
- Adapter in use:
- CLP schema_version:
- OS:

## Relevant Output

```
paste any error messages or hook output here
```

## Capsule / Checkpoint State

If the failure involved an active capsule or checkpoint, include the relevant fields (redact sensitive paths):

```yaml

```

## Additional Context

Config, hook settings, or related issues.
