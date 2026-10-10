# 0001: Docstring/Comment Language Scope (2026-10-07)

**Decision:** Inline code comments and docstrings throughout `src/` and `scripts/` stay German
for this review round. Only `docs/`, `README.md`, `CONTEXT.md`, and the workstream READMEs were
translated to English.

**Rationale:** The three reviewers assess methodology, documentation, and code/architecture —
all of which are now covered by the English `docs/` tree, `CONTEXT.md`, and `README.md`. Code
comments are internal implementation detail that reviewers are not expected to read line-by-line.
Translating the entire codebase's comments is a separate, much larger effort than the other five
items in this cleanup round and is deferred to a future, dedicated plan rather than scoped here.
