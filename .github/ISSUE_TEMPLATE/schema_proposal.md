---
name: Schema Proposal
about: Propose a new schema or significant changes to an existing one
labels: schema
assignees: ''
---

## Schema Name

Which schema does this affect or what is the new schema called?

## Motivation

Why does this schema change need to exist? What operational pain does it address?

## Proposed Fields

```yaml
# Show the new or changed fields with comments
field_name:  # description of the field
```

## Identity Fields

Confirm the schema includes or preserves the required identity envelope:

- [ ] `*_id` (unique identifier)
- [ ] `schema_version`
- [ ] `created_at`
- [ ] `updated_at`
- [ ] Lineage field (`parent_*_id` where applicable)
- [ ] `status` (if lifecycle state applies)

## Migration Notes

If existing consumers need to update, describe what changes:

## Example

```yaml
# A realistic anonymized example of the schema in use
```

## Related Schemas

Does this change require updates to related schemas (e.g., WorkerHandoff referencing a new capsule field)?
