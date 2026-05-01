"""Scoped gateway lock cleanup tests."""


def test_release_all_scoped_locks_accepts_owner_filters(tmp_path, monkeypatch):
    from gateway import status

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setattr(status, "_get_lock_dir", lambda: lock_dir)

    owned = lock_dir / "platform-owned.lock"
    other = lock_dir / "platform-other.lock"
    status._write_json_file(owned, {"pid": 123, "start_time": 456})
    status._write_json_file(other, {"pid": 999, "start_time": 888})

    removed = status.release_all_scoped_locks(owner_pid=123, owner_start_time=456)

    assert removed == 1
    assert not owned.exists()
    assert other.exists()


def test_release_all_scoped_locks_without_filters_removes_all(tmp_path, monkeypatch):
    from gateway import status

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setattr(status, "_get_lock_dir", lambda: lock_dir)
    (lock_dir / "a.lock").write_text("{}", encoding="utf-8")
    (lock_dir / "b.lock").write_text("{}", encoding="utf-8")

    assert status.release_all_scoped_locks() == 2
    assert not list(lock_dir.glob("*.lock"))
