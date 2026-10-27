import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).with_name("check-docs.py")


class DocumentationCheckTests(unittest.TestCase):
    def run_check(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_public_links_and_heading_anchors_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "README.md").write_text(
                "# App\n[Guide](docs/guide.md#use-the-cli)\n"
            )
            (root / "docs/guide.md").write_text(
                "# Guide\n## Use the `CLI`\n[Home](../README.md)\n"
            )
            result = self.run_check(root)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("PASS: 2 public Markdown documents", result.stdout)

    def test_private_task_and_plan_references_fail(self) -> None:
        for text in (
            "Complete GG-005.07.",
            "[Plan](IMPLEMENTATION_PLAN.md)",
            "See PSTACK_PROJECT_PROMPTS.txt.",
        ):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "README.md").write_text(text)
                result = self.run_check(root)
                self.assertEqual(result.returncode, 1)
                self.assertIn("private planning reference", result.stdout)

    def test_missing_file_and_heading_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "# App\n[Missing](missing.md)\n[Heading](#missing)\n"
            )
            result = self.run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing local link: missing.md", result.stdout)
            self.assertIn("missing heading anchor: #missing", result.stdout)

    def test_existing_private_artifact_and_sibling_checkout_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/verification").mkdir(parents=True)
            (root / "data/verification/receipt.md").write_text("# Receipt")
            (root / "README.md").write_text(
                "[Receipt](data/verification/receipt.md)\n[Sibling](../another-project/README.md)\n"
            )
            result = self.run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("link depends on private local artifact", result.stdout)
            self.assertIn("link leaves repository", result.stdout)

    def test_duplicate_headings_and_external_links_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "# App\n## Run\n## Run\n[Second](#run-1)\n[External](https://example.invalid/guide)\n"
            )
            result = self.run_check(root)
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_example_and_comment_ids_are_not_rendered_anchors(self) -> None:
        for example in (
            '```html\n<div id="example">\n```',
            '<!-- <div id="example"> -->',
            '`<div id="example">`',
        ):
            with (
                self.subTest(example=example),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "README.md").write_text(
                    "# App\n[Example](#example)\n" + example
                )
                result = self.run_check(root)
                self.assertEqual(result.returncode, 1)
                self.assertIn("missing heading anchor: #example", result.stdout)

    def test_machine_local_url_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("[Private](file:///Users/example/notes.md)")
            result = self.run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("link depends on local machine", result.stdout)


if __name__ == "__main__":
    unittest.main()
