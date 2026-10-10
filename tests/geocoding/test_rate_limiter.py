from __future__ import annotations

from unittest.mock import patch

import pytest

from github_collector.geocoding.rate_limiter import RateLimiter


def test_first_call_does_not_sleep():
    limiter = RateLimiter(min_interval_seconds=1.0)

    with (
        patch(
            "github_collector.geocoding.rate_limiter.time.monotonic",
            return_value=10.0,
        ) as mono,
        patch("github_collector.geocoding.rate_limiter.time.sleep") as sleep_mock,
    ):
        limiter.wait_for_api()

    mono.assert_called_once()
    sleep_mock.assert_not_called()


def test_second_immediate_call_sleeps_for_remaining_interval():
    limiter = RateLimiter(min_interval_seconds=1.0)

    with (
        patch(
            "github_collector.geocoding.rate_limiter.time.monotonic",
        ) as mono,
        patch("github_collector.geocoding.rate_limiter.time.sleep") as sleep_mock,
    ):
        # First call at t=10
        mono.return_value = 10.0
        limiter.wait_for_api()

        # Second call immediately afterwards at t=10.2 -> remaining 0.8s
        mono.return_value = 10.2
        limiter.wait_for_api()

    sleep_mock.assert_called_once()
    args, _ = sleep_mock.call_args
    assert pytest.approx(args[0], rel=1e-6) == 0.8


def test_second_call_after_interval_does_not_sleep():
    limiter = RateLimiter(min_interval_seconds=1.0)

    with (
        patch(
            "github_collector.geocoding.rate_limiter.time.monotonic",
        ) as mono,
        patch("github_collector.geocoding.rate_limiter.time.sleep") as sleep_mock,
    ):
        # First call at t=10
        mono.return_value = 10.0
        limiter.wait_for_api()

        # Second call at t=11.5 -> no remaining time
        mono.return_value = 11.5
        limiter.wait_for_api()

    sleep_mock.assert_not_called()
