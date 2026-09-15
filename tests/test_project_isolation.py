from __future__ import annotations

from pathlib import Path

import config.settings as settings_module
import config.paths as paths_module
from config.paths import PROJECT_ROOT, resolve_project_path
from config.settings import Settings


ENV_KEYS = ("SITE_USER", "SITE_PASSWORD", "GOOGLE_CREDENTIALS_PATH")


def _clear_test_environment(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_project_root_is_independent_of_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert PROJECT_ROOT == Path(settings_module.__file__).resolve().parents[1]


def test_relative_path_resolves_against_project_root():
    assert resolve_project_path("credentials/test.json") == (
        PROJECT_ROOT / "credentials/test.json"
    ).resolve()


def test_absolute_path_remains_absolute(tmp_path):
    absolute = (tmp_path / "google.json").resolve()
    assert resolve_project_path(absolute) == absolute


def test_explicit_env_file_is_loaded_and_credentials_are_project_relative(
    tmp_path, monkeypatch
):
    _clear_test_environment(monkeypatch)
    env_path = tmp_path / "isolated.env"
    env_path.write_text(
        "SITE_USER=explicit-user\n"
        "SITE_PASSWORD=test-only\n"
        "GOOGLE_CREDENTIALS_PATH=credentials/test.json\n",
        encoding="utf-8",
    )

    settings = Settings.from_env(env_path)

    assert settings.site_user == "explicit-user"
    assert settings.google_credentials_path == str(
        (PROJECT_ROOT / "credentials/test.json").resolve()
    )


def test_relative_env_path_is_resolved_against_project_root(tmp_path, monkeypatch):
    _clear_test_environment(monkeypatch)
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.env").write_text(
        "SITE_USER=relative-env\nSITE_PASSWORD=test-only\n", encoding="utf-8"
    )

    settings = Settings.from_env("config/local.env")

    assert settings.site_user == "relative-env"


def test_env_in_external_cwd_is_not_loaded(tmp_path, monkeypatch):
    _clear_test_environment(monkeypatch)
    external_project = tmp_path / "outro-projeto"
    external_project.mkdir()
    (external_project / ".env").write_text(
        "SITE_USER=wrong-project\nSITE_PASSWORD=wrong-project\n", encoding="utf-8"
    )
    monkeypatch.chdir(external_project)
    monkeypatch.setattr(
        settings_module, "DEFAULT_ENV_PATH", tmp_path / "missing-project.env"
    )

    settings = Settings.from_env()

    assert settings.site_user == ""
    assert settings.site_password == ""
