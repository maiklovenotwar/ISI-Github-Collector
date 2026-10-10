# GitHub API & GraphQL Component

## Overview

`src/github_collector/api/github_api.py` handles communication with the
GitHub REST API v3. A separate GraphQL handler
(`src/github_collector/enrichment/graphql_handler.py`) is used specifically
for repository-statistics enrichment. Both provide token-pool management,
caching, and error handling.

Three classes make up the REST component:

- `GitHubClient`: a single-token client (authentication, rate-limit
  tracking, caching, the actual HTTP request).
- `GitHubTokenPool`: tracks remaining quota per token and selects the best
  available one.
- `GitHubAPI`: the public interface, combining a token pool with one
  `GitHubClient` per token. This is the class application code uses.

## GraphQL Integration and Error Handling

- Repository-statistics enrichment uses the GitHub GraphQL API (see
  `enrichment/graphql_handler.py`).
- A failure on one repository does not abort the rest of the batch; it is
  logged individually.
- Stack traces and detailed error causes are recorded in the log file.
- Transient API errors (e.g. HTTP 502) are retried automatically.
- IDs of repositories that fail permanently are logged and can be
  reprocessed with `--retry-failed` (see `docs/usage.md`).

## Core Functionality

### 1. Token-Pool Management

`GitHubAPI` accepts one or more tokens and always routes the next request
through the token with the most remaining quota.

```python
api_client = GitHubAPI(["token1", "token2", "token3"])
```

### 2. Rate-Limit Handling

- Waits automatically when a token's rate limit is nearly exhausted
- Switches to another token with available quota, if one exists
- Tracks the limit, remaining quota, and reset time per token
  (`get_rate_limit()`, `monitor_rate_limit()`)

### 3. Caching

`GitHubClient` supports disk-based caching of API responses, keyed by
endpoint and request parameters.

```python
api_client = GitHubAPI(["token"], cache_dir="api_cache")
```

### 4. Error Handling

- Automatic retries on transient failures
- Specific exception types for different failure modes
  (`GitHubAPIError`, `GitHubRateLimitError`, `GitHubAuthenticationError`)

## Public Methods

### Generic

- `request(method, endpoint, params=None, data=None)`: issues a request
  through the best available token

### Repositories

- `get_repository(owner, repo)`
- `get_repositories(since=None, per_page=None)`
- `search_repositories(query, sort="stars", order="desc", per_page=None, page=1)`
- `get_repository_contributors(owner, repo, per_page=None, page=1)`
- `get_repository_contributors_count(owner, repo)` — uses a `HEAD` request,
  cheaper than fetching the full contributor list
- `get_repository_commits(owner, repo, per_page=None, page=1)`

### Users

- `get_contributor(username)`

### Organizations

- `get_organization(org_name)`
- `get_organization_repositories(org_name, per_page=None, page=1)`

### Rate Limit / Token Pool

- `get_rate_limit()`
- `monitor_rate_limit(threshold_percent=None)`
- `get_best_token()` (on `GitHubTokenPool`)
- `update_token_info(token, remaining, reset_time)` (on `GitHubTokenPool`)

## Usage

### Basic

```python
from github_collector.api.github_api import GitHubAPI

api_client = GitHubAPI(["your_github_token"])

repo_data = api_client.get_repository("octocat", "Hello-World")
contributor_data = api_client.get_contributor("octocat")
org_data = api_client.get_organization("github")
```

### With Multiple Tokens and Caching

```python
from github_collector.api.github_api import GitHubAPI

api_client = GitHubAPI(
    tokens=["token1", "token2", "token3"],
    cache_dir="api_cache",
)

repos = api_client.search_repositories(
    query="language:python stars:>1000",
    sort="stars",
    order="desc",
    per_page=100,
)

contributors = api_client.get_repository_contributors("octocat", "Hello-World")
commits = api_client.get_repository_commits("octocat", "Hello-World")
```

## Configuration

`GitHubAPI(tokens, cache_dir=None)`:

- `tokens`: a single token string, or a list of tokens
- `cache_dir`: directory for the on-disk response cache (optional; caching
  is disabled if omitted)

There is no `user_agent`, `timeout`, `max_retries`, or `retry_delay`
constructor parameter — the `User-Agent` header is fixed
(`GitHub-Data-Collector`), and retry behavior is implemented internally in
`request()`.

## Rate Limits (GitHub API)

- Authenticated requests: 5,000 requests/hour per token
- Unauthenticated requests: 60 requests/hour
- Search API: 30 requests/minute

The token pool amortizes these limits across however many tokens are
configured.

## Security Considerations

1. Tokens are not written to logs in plain text.
2. All API requests use HTTPS.
3. The application only requires read access to public repository data.
