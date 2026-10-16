"""Sprite workers must not block warm UI lookups or undo download invalidation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from Ankimon.functions import sprite_functions as sf


@pytest.fixture(autouse=True)
def sprite_root(monkeypatch, tmp_path):
    root = tmp_path / "sprites"
    root.mkdir()
    monkeypatch.setattr(sf, "pkmnimgfolder", root)
    sf._clear_sprite_cache()
    yield root
    sf._clear_sprite_cache()


@pytest.mark.parametrize("filesystem_call", ["realpath", "exists"])
@pytest.mark.parametrize("warm_exists", [True, False])
def test_warm_lookup_finishes_while_worker_checks_another_path(
    monkeypatch, sprite_root, filesystem_call, warm_exists
):
    warm = sprite_root / "25.png"
    cold = sprite_root / "26.png"
    if warm_exists:
        warm.touch()
    cold.touch()
    expected = str(warm) if warm_exists else None
    assert sf._get_cached_valid_path(str(warm)) == expected

    checking = Event()
    release = Event()
    original = getattr(sf.os.path, filesystem_call)

    def blocked_check(path, *args, **kwargs):
        if str(path) == str(cold):
            checking.set()
            assert release.wait(5), "Test did not release the cold lookup"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sf.os.path, filesystem_call, blocked_check)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(sf._get_cached_valid_path, str(cold))
        try:
            assert checking.wait(5), "Worker never reached the filesystem check"
            # Completion before release proves this cannot wait on worker I/O.
            warm_lookup = pool.submit(sf._get_cached_valid_path, str(warm))
            assert warm_lookup.result(timeout=2) == expected
            assert not worker.done()
        finally:
            release.set()
        assert worker.result(timeout=5) == str(cold)


@pytest.mark.parametrize("initially_exists", [True, False])
@pytest.mark.parametrize("refresh_before_release", [True, False])
def test_clear_during_lookup_does_not_restore_old_result(
    monkeypatch, sprite_root, initially_exists, refresh_before_release
):
    candidate = sprite_root / "25.png"
    if initially_exists:
        candidate.touch()
    path = str(candidate)
    checking = Event()
    release = Event()
    original_exists = sf.os.path.exists

    def blocked_first_check(checked_path):
        result = original_exists(checked_path)
        if checked_path == path and not checking.is_set():
            checking.set()
            assert release.wait(5), "Test did not release the old lookup"
        return result

    monkeypatch.setattr(sf.os.path, "exists", blocked_first_check)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old_lookup = pool.submit(sf._get_cached_valid_path, path)
        try:
            assert checking.wait(5), "Worker never checked the original file"
            if initially_exists:
                candidate.unlink()
            else:
                candidate.touch()
            pool.submit(sf._clear_sprite_cache).result(timeout=2)
            current = None if initially_exists else path
            if refresh_before_release:
                assert (
                    pool.submit(sf._get_cached_valid_path, path).result(timeout=2)
                    == current
                )
        finally:
            release.set()

        assert old_lookup.result(timeout=5) == (path if initially_exists else None)

    if refresh_before_release:
        assert sf._PATH_VALIDITY_CACHE[path] == current
    else:
        assert path not in sf._PATH_VALIDITY_CACHE
    assert sf._get_cached_valid_path(path) == current


def test_concurrent_hits_misses_and_eviction_keep_correct_bounded_entries(
    monkeypatch, sprite_root
):
    worker_count = 8
    monkeypatch.setattr(sf, "_SPRITE_CACHE_MAXSIZE", 3)
    paths = [str(sprite_root / f"{index}.png") for index in range(worker_count)]
    for index in range(0, worker_count, 2):
        (sprite_root / f"{index}.png").touch()
    start_round = Barrier(worker_count, timeout=5)

    def lookup(index):
        expected = paths[index] if index % 2 == 0 else None
        for _ in range(30):
            start_round.wait()
            assert sf._get_cached_valid_path(paths[index]) == expected
            # Exercise warm hits alongside other threads inserting and evicting.
            assert sf._get_cached_valid_path(paths[index]) == expected

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(lookup, index) for index in range(worker_count)]
        for future in futures:
            future.result(timeout=10)

    assert len(sf._PATH_VALIDITY_CACHE) <= 3
    for path, value in sf._PATH_VALIDITY_CACHE.items():
        assert value == (path if paths.index(path) % 2 == 0 else None)
