# Pydantic Schemas — Data Validation

## Overview

The Pydantic schemas form a validation layer between the GitHub API and the
database: all repository and owner data is validated and normalized before
it is stored.

```
GitHub API response → Pydantic schema → Normalized data → Database
```

**Location:** `src/github_collector/schemas/`

| File | Contents |
|------|----------|
| `common.py` | Base classes `GitHubEntity`, `TimestampMixin` |
| `owner.py` | `OwnerSchema`, `OwnerType` enum |
| `repository.py` | `RepositorySchema`, with a nested owner |

---

## 1. OwnerSchema

Validates a GitHub repository owner (user or organization).

### Fields

| Field | Type | Required | Description |
|-------|------|----------|--------------|
| `id` | int | yes | GitHub ID (>= 1) |
| `login` | str | yes | Login name (1-39 characters) |
| `type` | OwnerType | yes | User, Organization, Bot |
| `avatar_url` | str | no | Avatar URL |
| `html_url` | str | no | Profile URL |

### Normalization

- `login` is lowercased for consistent comparisons.
- URL fields must start with `http://` or `https://`; otherwise they are
  set to `None`.

### Example

```python
from github_collector.schemas import OwnerSchema

owner = OwnerSchema(
    id=1234,
    login="TestUser",  # becomes "testuser"
    type="User",
)
print(owner.login)  # "testuser"
```

---

## 2. RepositorySchema

Validates repository data from the GitHub Search/Repos API.

### Fields

| Category | Field | Type | Required | Description |
|----------|-------|------|----------|--------------|
| Core | `id` | int | yes | GitHub ID |
| | `name` | str | yes | Repository name (1-100 characters) |
| | `full_name` | str | yes | `owner/repo` (1-255 characters) |
| | `owner` | OwnerSchema | yes | Nested owner schema |
| Metadata | `private` | bool | no | Private repository (default: False) |
| | `description` | str | no | Description (max 65,535 characters) |
| | `fork` | bool | no | Is a fork (default: False) |
| Stats | `stargazers_count` | int | no | Stars (>= 0) |
| | `watchers_count` | int | no | Watchers (>= 0) |
| | `forks_count` | int | no | Forks (>= 0) |
| | `open_issues_count` | int | no | Open issues (>= 0) |
| Tech | `language` | str | no | Primary language (max 50 characters) |
| | `default_branch` | str | no | Default branch (default: "main") |
| Timestamps | `created_at` | str | no | ISO 8601 creation date |
| | `updated_at` | str | no | ISO 8601 last-update date |
| | `pushed_at` | str | no | ISO 8601 last-push date |

### Normalization

- `description`: whitespace is trimmed; truncated to 65,535 characters
  (the MySQL `TEXT` column limit).
- `language`: empty strings become `None`.
- Count fields reject negative values (`ge=0`).

### Example

```python
from github_collector.schemas import RepositorySchema

repo = RepositorySchema(
    id=12345,
    name="awesome-project",
    full_name="user/awesome-project",
    owner={"id": 1, "login": "User", "type": "User"},
    stargazers_count=500,
    description="  Some description with whitespace  ",  # trimmed
)
print(repo.description)  # "Some description with whitespace"
print(repo.owner.login)  # "user" (lowercase)
```

---

## 3. Integration in the Collector

The schemas are applied in `RepositoryCollector._process_repositories_batch()`:

```python
def _validate_repositories(self, repos: List[Dict]) -> List[Dict]:
    """Validate repositories against the Pydantic schema."""
    validated = []
    for repo in repos:
        try:
            schema = RepositorySchema(**repo)
            validated.append(schema.model_dump(mode='json'))
        except ValidationError as e:
            logger.warning(f"Skipping invalid repository {repo.get('full_name')}: {e}")
    return validated
```

### Logging on Validation Failure

```
WARNING - Skipping invalid repository user/broken-repo (ID=123): 2 validation error(s)
WARNING - Validation filtered out 1/100 repositories (1.0%)
```

---

## 4. Configuration

All schemas share the same Pydantic configuration:

```python
model_config = ConfigDict(
    extra="ignore",        # Ignore unknown fields
    use_enum_values=True,  # Serialize enum values as strings
)
```

`extra="ignore"` is important because the GitHub API frequently adds or
changes fields; without it, new API fields would cause validation errors.

---

## 5. Tests

**Test file:** `tests/unit/test_schemas.py` (10 tests)

| Test | Focus |
|------|-------|
| `test_valid_user_owner` | Valid user owner |
| `test_valid_org_owner` | Valid organization owner |
| `test_login_normalization` | Lowercase normalization |
| `test_invalid_url` | URL validation |
| `test_valid_repository` | Complete repository |
| `test_description_normalization` | Whitespace trimming |
| `test_negative_stargazers_rejected` | Count validation |
| `test_helper_methods` | `get_owner_id()` and similar helpers |
| `test_missing_required_fields` | Required-field validation |
| `test_extra_fields_ignored` | `extra="ignore"` behavior |

Run with:

```bash
pytest tests/unit/test_schemas.py -v
```

---

## 6. Known Limitations

1. **No timestamp parsing**: timestamps are accepted as `str`, not
   `datetime`; conversion happens later in the database layer.
2. **License as a raw dict**: the `license` field is an untyped `dict`,
   not a nested schema.
3. **No API-version distinction**: schemas accept data from any GitHub API
   version (REST v3, GraphQL) without differentiating between them.
