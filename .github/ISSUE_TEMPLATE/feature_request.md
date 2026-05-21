---
name: Feature Request
about: Suggest an improvement or new capability
labels: enhancement
assignees: ''
---

## Summary

A one-sentence description of the feature.

## Problem It Solves

What is currently difficult or impossible that this would fix?

## Proposed Solution

How you imagine it working. Include schema examples or config snippets if relevant.

## Affected Layer

Which part of CLP does this touch?

- [ ] InvestigationCapsule schema
- [ ] LoopCheckpoint schema
- [ ] WorkerHandoff schema
- [ ] SessionLease / worker_scope
- [ ] ContextGuard core policy
- [ ] Claude Code adapter
- [ ] New runtime adapter
- [ ] `.context/` layout
- [ ] Templates / examples
- [ ] Documentation

## Runtime / Adapter Context

Which runtime or agent framework is this for? Is this generic or adapter-specific?

## Alternatives Considered

Other approaches and why you ruled them out.

## Lifecycle Invariant Check

Confirm this change preserves the core lifecycle invariants:

- [ ] Capsules retain identity fields
- [ ] Workers remain disposable (not immortal)
- [ ] Enforcement remains in ContextGuard, not in schemas
- [ ] Architecture remains runtime/vendor agnostic

## Additional Context

Related issues, prior discussion, or example consuming repos.
