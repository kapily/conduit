import copy
import tempfile
import unittest
import zipfile
from pathlib import Path

from apk_manifest import inspect, validate, validate_pair


class ApkManifestTest(unittest.TestCase):
    def test_archive_hashes_distinguish_the_app_from_its_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            manifests = []
            for build in ("baseline", "candidate"):
                apk = Path(directory) / (build + ".apk")
                with zipfile.ZipFile(apk, "w") as archive:
                    archive.writestr("lib/x86_64/libapp.so", build)
                    archive.writestr("lib/x86_64/libflutter.so", "same engine")
                manifests.append(inspect(apk))
            validate_pair(*manifests)
            with self.assertRaisesRegex(AssertionError, "identical AOT"):
                validate_pair(manifests[0], manifests[0])
            changed = copy.deepcopy(manifests[1])
            changed["entries"][1]["sha256"] = "different engine"
            with self.assertRaisesRegex(AssertionError, "engines differ"):
                validate_pair(manifests[0], changed)

    def test_missing_aot_or_duplicate_entries_cannot_pass(self):
        engine = {"name": "lib/x86_64/libflutter.so", "size": 1}
        with self.assertRaisesRegex(AssertionError, "Missing x64 runtime: libapp"):
            validate({"entries": [engine]})
        with self.assertRaisesRegex(AssertionError, "Duplicate APK entries"):
            validate({"entries": [engine, engine]})

    def test_other_architectures_cannot_pass(self):
        entries = [
            {"name": name, "size": 1}
            for name in (
                "lib/x86_64/libapp.so",
                "lib/x86_64/libflutter.so",
                "lib/arm64-v8a/libapp.so",
            )
        ]
        with self.assertRaisesRegex(AssertionError, "Unexpected native ABI"):
            validate({"entries": entries})


if __name__ == "__main__":
    unittest.main()
