"""Tests for the handling of GitHub tokens rejected with HTTP 401.

GitHub sends no rate-limit headers with a 401 response, so the remaining budget of
an invalid token never decreases. Without explicit bookkeeping the token pool would
keep preferring such a token.
"""

import logging
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from github_collector.api.github_api import (
    GitHubAPI,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubTokenPool,
)

BAD = "ghp_" + "b" * 30 + "BADBAD"
GOOD = "ghp_" + "g" * 30 + "GOODOK"


class TestTokenPoolInvalidTokens:
    def test_invalid_token_is_never_selected_again(self):
        pool = GitHubTokenPool([BAD, GOOD])

        pool.mark_invalid(BAD)

        assert {pool.get_best_token() for _ in range(20)} == {GOOD}

    def test_invalid_token_is_skipped_even_with_the_largest_remaining_budget(self):
        pool = GitHubTokenPool([BAD, GOOD])
        future = int(time.time()) + 3600
        pool.update_token_info(BAD, remaining=5000, reset_time=future)
        pool.update_token_info(GOOD, remaining=1, reset_time=future)

        pool.mark_invalid(BAD)

        assert pool.get_best_token() == GOOD

    def test_all_tokens_invalid_raises_authentication_error(self):
        pool = GitHubTokenPool([BAD, GOOD])
        pool.mark_invalid(BAD)
        pool.mark_invalid(GOOD)

        with pytest.raises(GitHubAuthenticationError):
            pool.get_best_token()

    def test_marking_the_same_token_twice_does_not_invalidate_the_pool(self):
        pool = GitHubTokenPool([BAD, GOOD])

        pool.mark_invalid(BAD)
        pool.mark_invalid(BAD)

        assert pool.get_best_token() == GOOD

    def test_rate_limited_pool_waits_for_a_valid_token_not_an_invalid_one(self):
        pool = GitHubTokenPool([BAD, GOOD])
        now = int(time.time())
        # The invalid token would reset first and must still not be chosen.
        pool.update_token_info(BAD, remaining=0, reset_time=now + 60)
        pool.update_token_info(GOOD, remaining=0, reset_time=now + 600)
        pool.mark_invalid(BAD)

        with patch("github_collector.api.github_api.time.sleep") as sleep_mock:
            token = pool.get_best_token()

        assert token == GOOD
        assert sleep_mock.called

    def test_log_message_does_not_contain_the_full_token(self, caplog):
        pool = GitHubTokenPool([BAD, GOOD])

        with caplog.at_level(logging.ERROR, logger="github_collector.api.github_api"):
            pool.mark_invalid(BAD)

        assert BAD not in caplog.text
        assert BAD[-6:] in caplog.text


class TestClientRaisesOnUnauthorized:
    @staticmethod
    def _response(status_code: int, text: str) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.text = text
        response.headers = {}
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(str(status_code))
        return response

    def test_401_raises_authentication_error(self):
        client = GitHubClient(BAD)
        client.session.request = MagicMock(return_value=self._response(401, "Bad credentials"))

        with pytest.raises(GitHubAuthenticationError):
            client.request("GET", "rate_limit", use_cache=False)

    def test_other_client_errors_are_not_reported_as_authentication_errors(self):
        client = GitHubClient(GOOD)
        client.session.request = MagicMock(return_value=self._response(422, "Unprocessable"))

        with pytest.raises(GitHubAPIError) as excinfo:
            client.request("GET", "rate_limit", use_cache=False)

        assert not isinstance(excinfo.value, GitHubAuthenticationError)


class TestApiRotatesAwayFromInvalidTokens:
    @staticmethod
    def _api(responses: dict) -> GitHubAPI:
        """Build an API whose clients answer with the given result or exception per token."""
        api = GitHubAPI(list(responses))
        for token, outcome in responses.items():
            if isinstance(outcome, Exception):
                api.clients[token].request = MagicMock(side_effect=outcome)
            else:
                api.clients[token].request = MagicMock(return_value=outcome)
        return api

    def test_request_continues_with_the_next_token_without_waiting(self):
        api = self._api({BAD: GitHubAuthenticationError("401"), GOOD: {"ok": True}})
        # Make the invalid token the first choice.
        api.token_pool.token_info[GOOD]["last_used"] = 1.0

        with patch("github_collector.api.github_api.time.sleep") as sleep_mock:
            result = api.request("GET", "rate_limit", retries=3)

        assert result == {"ok": True}
        assert api.clients[BAD].request.call_count == 1
        assert BAD in api.token_pool._invalid_tokens
        sleep_mock.assert_not_called()

    def test_invalid_token_is_not_used_by_later_requests(self):
        api = self._api({BAD: GitHubAuthenticationError("401"), GOOD: {"ok": True}})
        api.token_pool.token_info[GOOD]["last_used"] = 1.0

        for _ in range(5):
            api.request("GET", "rate_limit", retries=3)

        assert api.clients[BAD].request.call_count == 1
        assert api.clients[GOOD].request.call_count == 5

    def test_request_raises_once_every_token_was_rejected(self):
        api = self._api(
            {BAD: GitHubAuthenticationError("401"), GOOD: GitHubAuthenticationError("401")}
        )

        with patch("github_collector.api.github_api.time.sleep") as sleep_mock:
            with pytest.raises(GitHubAuthenticationError):
                api.request("GET", "rate_limit", retries=10)

        # Each token is tried exactly once; the remaining retries are not spent.
        assert api.clients[BAD].request.call_count == 1
        assert api.clients[GOOD].request.call_count == 1
        sleep_mock.assert_not_called()

    def test_single_invalid_token_raises_on_the_last_attempt(self):
        api = self._api({BAD: GitHubAuthenticationError("401")})

        with pytest.raises(GitHubAuthenticationError):
            api.request("GET", "rate_limit", retries=1)

        assert BAD in api.token_pool._invalid_tokens
