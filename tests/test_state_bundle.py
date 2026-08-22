import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.deployment.state_bundle import (
    StateBundleError,
    create_state_manifest,
    validate_state_bundle,
)
from internship_monitor.notifications import NotificationQueueRepository
from internship_monitor.state import JobStateRepository


class StateBundleTests(TestCase):
    def _create_state(self, directory: Path) -> Path:
        state = directory / "state"
        state.mkdir()
        with JobStateRepository(state / "jobs.sqlite3"):
            pass
        with NotificationQueueRepository(state / "notifications.sqlite3"):
            pass
        create_state_manifest(state)
        return state

    def test_bundle_validates_and_survives_a_second_restore_cycle(self) -> None:
        with TemporaryDirectory() as directory:
            source = self._create_state(Path(directory))
            restored = Path(directory) / "restored"
            shutil.copytree(source, restored)

            validate_state_bundle(restored)

            with JobStateRepository(restored / "jobs.sqlite3") as repository:
                self.assertEqual(repository.listing_state_counts().total_known, 0)
            with NotificationQueueRepository(restored / "notifications.sqlite3") as repository:
                self.assertEqual(repository.queue_counts().delivered, 0)
            create_state_manifest(restored)
            validate_state_bundle(restored)

    def test_manifest_is_an_explicit_operational_database_allowlist(self) -> None:
        with TemporaryDirectory() as directory:
            state = self._create_state(Path(directory))
            (state / "config.local").write_text("secret", encoding="utf-8")
            (state / "rag.sqlite3").write_bytes(b"private")
            manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(set(manifest["files"]), {"jobs.sqlite3", "notifications.sqlite3"})
        self.assertNotIn("config.local", manifest["files"])
        self.assertNotIn("rag.sqlite3", manifest["files"])

    def test_missing_state_is_rejected_without_overwriting_prior_good_bundle(self) -> None:
        with TemporaryDirectory() as directory:
            state = self._create_state(Path(directory))
            good_manifest = (state / "manifest.json").read_bytes()
            (state / "notifications.sqlite3").unlink()

            with self.assertRaisesRegex(StateBundleError, "missing"):
                validate_state_bundle(state)

            self.assertEqual((state / "manifest.json").read_bytes(), good_manifest)

    def test_checksum_mismatch_is_rejected_before_sqlite_is_opened(self) -> None:
        with TemporaryDirectory() as directory:
            state = self._create_state(Path(directory))
            with (state / "jobs.sqlite3").open("ab") as stream:
                stream.write(b"changed")

            with self.assertRaisesRegex(StateBundleError, "checksum mismatch"):
                validate_state_bundle(state)

    def test_corrupt_sqlite_is_rejected_even_with_a_matching_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            state = self._create_state(Path(directory))
            jobs = state / "jobs.sqlite3"
            jobs.write_bytes(b"not a SQLite database")
            manifest_path = state / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["jobs.sqlite3"]["sha256"] = hashlib.sha256(
                jobs.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(StateBundleError, "not valid SQLite"):
                validate_state_bundle(state)

    def test_actions_artifact_root_layout_restores_into_state_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = self._create_state(root)
            archive = root / "internship-monitor-state.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name in ("manifest.json", "jobs.sqlite3", "notifications.sqlite3"):
                    bundle.write(state / name, arcname=name)

            restored_state = root / "actions-workspace" / "state"
            restored_state.mkdir(parents=True)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(restored_state)

            validate_state_bundle(restored_state)
            self.assertEqual(
                {path.name for path in restored_state.iterdir()},
                {"manifest.json", "jobs.sqlite3", "notifications.sqlite3"},
            )
            create_state_manifest(restored_state)
            self.assertEqual(
                {path.name for path in restored_state.iterdir()},
                {"manifest.json", "jobs.sqlite3", "notifications.sqlite3"},
            )
            (restored_state / "unexpected.txt").write_text("unexpected", encoding="utf-8")
            self.assertNotEqual(
                {path.name for path in restored_state.iterdir()},
                {"manifest.json", "jobs.sqlite3", "notifications.sqlite3"},
            )
            workspace = restored_state.parent
            self.assertFalse((workspace / "manifest.json").exists())
            self.assertFalse((workspace / "jobs.sqlite3").exists())
            self.assertFalse((workspace / "notifications.sqlite3").exists())
