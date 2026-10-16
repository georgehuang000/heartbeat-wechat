from pathlib import Path
import sys
from galchat.runtime_paths import data_directory, resource_root


def test_source_keeps_existing_project_data(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert (resource_root() / 'main.py').is_file()
    assert data_directory() == resource_root() / 'data'


def test_frozen_data_survives_temporary_bundle_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path / 'temporary-bundle'), raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'profile'))
    assert resource_root() == tmp_path / 'temporary-bundle'
    assert data_directory() == tmp_path / 'profile/HeartWeChat'
    assert data_directory() != resource_root() / 'data'


def test_explicit_data_dir_for_isolated_acceptance(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert data_directory(tmp_path / 'qa') == tmp_path / 'qa'
