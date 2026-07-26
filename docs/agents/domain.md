# Domain Docs

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- `docs/adr/` entries relevant to the work.

If these files do not exist, proceed silently. The domain-modeling skill creates them lazily when terminology or decisions are resolved.

## File structure

This is a single-context repository:

```text
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

Use terms as defined in `CONTEXT.md`. Avoid synonyms the glossary explicitly rejects. If a needed concept is missing, reconsider the terminology or note the gap for domain modeling.

## Flag ADR conflicts

Explicitly surface output that conflicts with an existing ADR rather than silently overriding the decision.
