"""Tests für Performance-Helfer (AdaptiveBatchManager)."""

import os
from unittest.mock import patch

from github_collector.utils.performance import AdaptiveBatchConfig, AdaptiveBatchManager


@patch.dict(os.environ, {"BATCH_INITIAL_SIZE": ""}, clear=False)
def test_initial_size_clamped_to_bounds() -> None:
    cfg = AdaptiveBatchConfig(min_size=10, max_size=100, initial_size=200)
    mgr = AdaptiveBatchManager(cfg)
    assert mgr.current_size == 100


@patch.dict(os.environ, {"BATCH_INITIAL_SIZE": ""}, clear=False)
def test_update_increases_size_when_faster_than_target() -> None:
    cfg = AdaptiveBatchConfig(
        target_duration=5.0,
        min_size=10,
        max_size=100,
        initial_size=50,
        increase_step=10,
        decrease_factor=0.5,
    )
    mgr = AdaptiveBatchManager(cfg)

    # Sehr schneller Batch
    new_size = mgr.update(duration=1.0, item_count=50)
    assert new_size == 60

    # Nochmals schneller Batch
    new_size = mgr.update(duration=0.5, item_count=60)
    assert new_size == 70


@patch.dict(os.environ, {"BATCH_INITIAL_SIZE": ""}, clear=False)
def test_update_decreases_size_when_slower_than_target() -> None:
    cfg = AdaptiveBatchConfig(
        target_duration=5.0,
        min_size=10,
        max_size=100,
        initial_size=80,
        increase_step=10,
        decrease_factor=0.5,
    )
    mgr = AdaptiveBatchManager(cfg)

    # Langsamer Batch
    new_size = mgr.update(duration=10.0, item_count=80)
    assert new_size == 40

    # Noch langsamer, darf min_size nicht unterschreiten
    new_size = mgr.update(duration=20.0, item_count=40)
    assert new_size == 20


@patch.dict(os.environ, {"BATCH_INITIAL_SIZE": ""}, clear=False)
def test_update_respects_min_and_max_bounds() -> None:
    cfg = AdaptiveBatchConfig(
        target_duration=5.0,
        min_size=10,
        max_size=100,
        initial_size=95,
        increase_step=10,
        decrease_factor=0.5,
    )
    mgr = AdaptiveBatchManager(cfg)

    # Erhöhung darf max_size nicht überschreiten
    new_size = mgr.update(duration=1.0, item_count=95)
    assert new_size == 100

    # Verringerung darf min_size nicht unterschreiten
    mgr.current_size = 12
    new_size = mgr.update(duration=20.0, item_count=12)
    assert new_size == 10


@patch.dict(os.environ, {"BATCH_INITIAL_SIZE": ""}, clear=False)
def test_update_ignores_invalid_measurements() -> None:
    cfg = AdaptiveBatchConfig(initial_size=50)
    mgr = AdaptiveBatchManager(cfg)

    # Ungültige Messungen verändern die Größe nicht
    assert mgr.update(duration=0.0, item_count=50) == 50
    assert mgr.update(duration=-1.0, item_count=50) == 50
    assert mgr.update(duration=1.0, item_count=0) == 50
