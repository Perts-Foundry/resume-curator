"""Tests for the publish helper (curator.publish.publish_artifacts)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from curator.exceptions import PublishError
from curator.publish import publish_artifacts
from curator.renderer import RENDER_PUBLISH_FILENAMES

if TYPE_CHECKING:
    from collections.abc import Iterable


def _make_profile(
    profile_dir: Path,
    filenames: Iterable[str] = RENDER_PUBLISH_FILENAMES,
) -> dict[str, Path]:
    """Create a fake profile dir with the given filenames as small text files."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name in filenames:
        path = profile_dir / name
        path.write_text(f"content of {name}\n", encoding="utf-8")
        written[name] = path
    return written


class TestPublishArtifacts:
    """publish_artifacts copies upload-ready files into <dest>/<profile>/."""

    def test_copies_all_present_files(self, tmp_path: Path) -> None:
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile)
        dest = tmp_path / "publish"

        paths = publish_artifacts(profile, dest)

        assert len(paths) == len(RENDER_PUBLISH_FILENAMES)
        for path in paths:
            assert path.is_file()
            assert path.parent == dest / "2026-05-27-acme"

    def test_skips_missing_files(self, tmp_path: Path) -> None:
        # Only resume.pdf present (the common no-cover-letter case).
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile, filenames=["resume.pdf"])
        dest = tmp_path / "publish"

        paths = publish_artifacts(profile, dest)

        assert [p.name for p in paths] == ["resume.pdf"]
        assert not (dest / "2026-05-27-acme" / "cover_letter.pdf").exists()
        assert not (dest / "2026-05-27-acme" / "cover_letter.txt").exists()

    def test_preserves_filename_order(self, tmp_path: Path) -> None:
        # Output order matches RENDER_PUBLISH_FILENAMES regardless of disk order.
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile)
        dest = tmp_path / "publish"

        paths = publish_artifacts(profile, dest)

        assert [p.name for p in paths] == list(RENDER_PUBLISH_FILENAMES)

    def test_creates_nested_profile_dir(self, tmp_path: Path) -> None:
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile, filenames=["resume.pdf"])
        dest = tmp_path / "deeply" / "nested" / "publish"  # doesn't exist

        paths = publish_artifacts(profile, dest)

        assert dest.is_dir()
        assert (dest / "2026-05-27-acme").is_dir()
        assert paths[0] == dest / "2026-05-27-acme" / "resume.pdf"

    def test_overwrites_existing_destination_files(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile, filenames=["resume.pdf"])
        dest = tmp_path / "publish"
        # Pre-existing file at destination should be replaced.
        (dest / "2026-05-27-acme").mkdir(parents=True)
        prior = dest / "2026-05-27-acme" / "resume.pdf"
        prior.write_text("old content")

        publish_artifacts(profile, dest)

        assert prior.read_text() == "content of resume.pdf\n"

    def test_expands_user_in_destination(self, tmp_path: Path) -> None:
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile, filenames=["resume.pdf"])
        # Use a real tmp_path but exercise expanduser by passing "~"-prefixed
        # via a synthetic HOME so we don't pollute the real ~/.
        import os

        old_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(tmp_path / "fake_home")
            paths = publish_artifacts(profile, Path("~/publish"))
            assert (tmp_path / "fake_home" / "publish").is_dir()
            assert paths[0].is_file()
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    def test_unwritable_destination_raises_publish_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "profile" / "2026-05-27-acme"
        _make_profile(profile, filenames=["resume.pdf"])

        err_msg = "disk on fire"

        def _fake_mkdir(*_args: object, **_kwargs: object) -> None:
            raise OSError(err_msg)

        monkeypatch.setattr(Path, "mkdir", _fake_mkdir)

        with pytest.raises(PublishError, match="Cannot create publish destination"):
            publish_artifacts(profile, tmp_path / "publish")

    def test_returns_empty_list_when_no_files_present(self, tmp_path: Path) -> None:
        # Empty profile dir -- no listed filenames present.
        profile = tmp_path / "profile" / "empty"
        profile.mkdir(parents=True)
        dest = tmp_path / "publish"

        paths = publish_artifacts(profile, dest)

        assert paths == []
        # Destination dir is still created (idempotency on re-runs).
        assert (dest / "empty").is_dir()

    def test_missing_profile_dir_raises_publish_error(self, tmp_path: Path) -> None:
        # Defense-in-depth guard: helper raises rather than silently
        # creating an empty publish subdir when the source doesn't exist.
        missing = tmp_path / "profile" / "ghost"

        with pytest.raises(PublishError, match="Source profile directory not found"):
            publish_artifacts(missing, tmp_path / "publish")

    def test_parent_traversal_in_profile_name_rejected(self, tmp_path: Path) -> None:
        # ``Path("..").name == ".."``; without a guard the helper would
        # have built ``<dest>/..`` and written outside the destination
        # root. Mirrors the renderer.py is_relative_to guard pattern.
        sibling = tmp_path / "publish-sibling"
        sibling.mkdir()
        # Create the directory whose .name is ".." by walking up from a
        # real path so is_dir() passes.
        parent_path = tmp_path / "wrapper" / ".."
        (tmp_path / "wrapper").mkdir()

        with pytest.raises(PublishError, match="would write outside"):
            publish_artifacts(parent_path, tmp_path / "publish")

    def test_symlink_in_profile_dir_not_followed(self, tmp_path: Path) -> None:
        # Defense in depth: a symlink named resume.pdf pointing to an
        # off-target file (e.g. ~/.ssh/id_rsa) must not be dereferenced.
        # follow_symlinks=False causes copy2 to copy the link itself.
        profile = tmp_path / "profile" / "linky"
        profile.mkdir(parents=True)
        off_target = tmp_path / "secret.txt"
        off_target.write_text("SECRET PAYLOAD\n")
        (profile / "resume.pdf").symlink_to(off_target)
        dest = tmp_path / "publish"

        paths = publish_artifacts(profile, dest)

        assert paths == [dest / "linky" / "resume.pdf"]
        assert paths[0].is_symlink()
        # The link target text should not have been copied as file content.
        # (It would still resolve via the link, but the destination is
        # itself a symlink rather than an independent copy of the secret.)
