import datetime
import os
import sys

import pytest
import yaml

import confuse

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def test_toml_source_types_and_metadata(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        'title = "caf\u00e9"\nwhen = 2026-11-23\n[server]\nport = 8000\n'
        'enabled = true\ntags = ["a", "b"]\n',
        encoding="utf-8",
    )
    source = confuse.TomlSource(path, default=True, base_for_paths=True)
    assert source["title"] == "caf\u00e9"
    assert source["when"] == datetime.date(2026, 11, 23)
    assert source["server"] == {"port": 8000, "enabled": True, "tags": ["a", "b"]}
    assert source.filename == str(path)
    assert source.default
    assert source.base_for_paths


def test_toml_optional_and_empty_files(tmp_path):
    path = tmp_path / "missing.toml"
    assert confuse.TomlSource(path, optional=True) == {}
    assert confuse.TomlSource(optional=True) == {}
    with pytest.raises(TypeError, match="filename"):
        confuse.TomlSource()
    with pytest.raises(confuse.ConfigReadError, match=r"missing\.toml"):
        confuse.TomlSource(path)
    path.write_text("", encoding="utf-8")
    assert confuse.TomlSource(path) == {}


@pytest.mark.parametrize("contents", [b"key = [", b"key = 1\nkey = 2", b'key = "\xff"'])
def test_toml_parse_errors_include_the_filename(tmp_path, contents):
    path = tmp_path / "broken.toml"
    path.write_bytes(contents)
    with pytest.raises(confuse.ConfigReadError, match=r"broken\.toml") as exc:
        confuse.TomlSource(path)
    assert exc.value.reason is not None


def test_mixed_sources_templates_paths_and_reload(tmp_path):
    path = tmp_path / "settings.TOML"
    path.write_text('[server]\nport = 8000\nfile = "data.txt"\n', encoding="utf-8")
    config = confuse.Configuration("mixed", read=False)
    config.add({"server": {"host": "localhost", "port": 80}})
    config.set_file(path, base_for_paths=True)
    assert config["server"].get({"host": str, "port": int}) == {
        "host": "localhost",
        "port": 8000,
    }
    assert config["server"]["file"].as_filename() == str(tmp_path / "data.txt")
    path.write_text("[server]\nport = 9000\n", encoding="utf-8")
    config.reload()
    assert config["server"]["port"].get(int) == 9000
    assert not config["server"]["file"].exists()
    path.write_text("bad = [", encoding="utf-8")
    with pytest.raises(confuse.ConfigReadError):
        config.reload()
    assert config["server"]["port"].get(int) == 9000


def test_toml_user_and_package_discovery(tmp_path, monkeypatch):
    user = tmp_path / "user"
    package = tmp_path / "package"
    user.mkdir()
    package.mkdir()
    (user / "config.toml").write_text("port = 8000\n", encoding="utf-8")
    (package / "config_default.toml").write_text(
        'port = 80\nhost = "localhost"\n', encoding="utf-8"
    )
    monkeypatch.setenv("TOMLAPPDIR", str(user))
    monkeypatch.setattr(confuse.util, "find_package_path", lambda _: str(package))
    config = confuse.Configuration("tomlapp", "example", format="toml")
    assert config.user_config_path() == str(user / "config.toml")
    assert config.flatten() == {"port": 8000, "host": "localhost"}
    assert all(isinstance(source, confuse.TomlSource) for source in config.sources)
    assert config.sources[-1].default


def test_toml_searches_the_config_directories(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    app = second / "example"
    app.mkdir(parents=True)
    (app / "config.toml").write_text('name = "chosen"\n', encoding="utf-8")
    monkeypatch.delenv("EXAMPLEDIR", raising=False)
    monkeypatch.setattr(confuse.util, "config_dirs", lambda: [str(first), str(second)])
    config = confuse.Configuration("example", format="toml")
    assert config.config_dir() == str(app)
    assert config["name"].get() == "chosen"


def test_lazy_toml_preserves_precedence(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('value = "file"\n', encoding="utf-8")
    monkeypatch.setenv("LAZYTOMLDIR", str(tmp_path))
    config = confuse.LazyConfig("lazytoml", format="toml")
    config.add({"fallback": True})
    config.set({"value": "override"})
    assert not config._materialized
    assert config.flatten() == {"value": "override", "fallback": True}


def test_toml_dump_round_trip_redaction_and_defaults(tmp_path):
    defaults = tmp_path / "defaults.toml"
    defaults.write_text('default = 1\nsecret = "private"\n', encoding="utf-8")
    config = confuse.Configuration("dump", read=False, format="toml")
    config.add(confuse.TomlSource(defaults, default=True))
    config.set({"port": 9000})
    config["secret"].redact = True
    assert tomllib.loads(config.dump()) == {
        "port": 9000,
        "default": 1,
        "secret": "private",
    }
    assert (
        tomllib.loads(config.dump(redact=True))["secret"] == confuse.REDACTED_TOMBSTONE
    )
    assert tomllib.loads(config.dump(full=False)) == {"port": 9000}
    assert yaml.safe_load(config.dump(format="yaml")) == config.flatten()


def test_yaml_defaults_still_use_yaml(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("value: yaml\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text('value = "toml"\n', encoding="utf-8")
    monkeypatch.setenv("YAMLAPPDIR", str(tmp_path))
    config = confuse.Configuration("yamlapp")
    assert config["value"].get() == "yaml"
    assert config.user_config_path() == os.fspath(tmp_path / "config.yaml")
    assert yaml.safe_load(config.dump()) == {"value": "yaml"}
    assert tomllib.loads(config.dump(format="toml")) == {"value": "yaml"}


def test_unknown_formats_are_rejected():
    with pytest.raises(ValueError, match="format"):
        confuse.Configuration("example", read=False, format="json")
    with pytest.raises(ValueError, match="format"):
        confuse.Configuration("example", read=False).dump(format="json")


def test_toml_dump_does_not_silently_drop_unsupported_values():
    config = confuse.Configuration("example", read=False, format="toml")
    config.set({"value": None})
    with pytest.raises(TypeError):
        config.dump()
