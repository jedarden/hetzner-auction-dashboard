"""Keep release identifiers synchronized across build and dashboard surfaces."""

from pathlib import Path

from pipeline import __version__


def test_release_version_is_consistent():
    project_root = Path(__file__).parents[2]
    release_version = (project_root / "VERSION").read_text(encoding="utf-8").strip()

    assert release_version == __version__
    assert f'version = "{release_version}"' in (
        project_root / "pipeline" / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert f'LABEL version="{release_version}"' in (
        project_root / "pipeline" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert f"v{release_version}</span>" in (
        project_root / "web" / "index.html"
    ).read_text(encoding="utf-8")
