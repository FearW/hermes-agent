from agent import l4_archive


def test_search_archive_matches_summary(tmp_path, monkeypatch):
    archive_dir = tmp_path / 'memory_l4'
    archive_json = archive_dir / 'archive.json'
    archive_db = archive_dir / 'archive.db'
    monkeypatch.setattr(l4_archive, 'ARCHIVE_DIR', archive_dir)
    monkeypatch.setattr(l4_archive, 'ARCHIVE_PATH', archive_json)
    monkeypatch.setattr(l4_archive, 'ARCHIVE_DB_PATH', archive_db)
    monkeypatch.setenv('TERMINAL_CWD', '/srv/projects/my-app')

    l4_archive.archive_session_summary(
        'sess-2',
        'telegram',
        [
            {'role': 'user', 'content': 'Fix docker networking bug in /srv/app/docker-compose.yml'},
            {'role': 'assistant', 'content': 'Adjusted bridge settings'},
        ],
    )
    matches = l4_archive.search_archive('docker networking compose', limit=2, project_tag='my-app')
    assert matches
    assert matches[0]['project_tag'] == 'my-app'
    assert matches[0]['category'] == 'devops'
    assert matches[0]['priority'] >= 1
    assert matches[0]['confidence'] >= 0.5
