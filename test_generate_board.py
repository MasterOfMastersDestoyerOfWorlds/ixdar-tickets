"""Tests for the ticket CLI, run against a throwaway copy of the tickets tree.

Run with `uv run pytest test_generate_board.py` or `python3 -m unittest test_generate_board`
from the tickets repo. Every test points generate_board at a temporary root, so the live
board is never touched.
"""

import argparse
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import generate_board


REPO_ROOT = Path(__file__).resolve().parent


def _run(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process and capture its exit code and streams.

    :param argv: command-line arguments after the program name.
    :return: (exit code, stdout text, stderr text).
    """
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = generate_board.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TicketCliTest(unittest.TestCase):
    """Exercises the ticket CLI against a temporary copy of content/ and done/."""

    def setUp(self) -> None:
        """Copy the tickets tree into a temporary root and point the module at it."""
        self._saved_root = generate_board.ROOT_DIR
        self._saved_code_dir = generate_board.CODE_DIR
        self.temp = Path(tempfile.mkdtemp(prefix="tix-test-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.addCleanup(lambda: generate_board.set_root_dir(self._saved_root))
        self.addCleanup(lambda: generate_board.set_code_dir(self._saved_code_dir))

        self.root = self.temp / "tickets"
        (self.root / "content").mkdir(parents=True)
        (self.root / "done").mkdir(parents=True)
        for name in ("content", "done"):
            source = REPO_ROOT / name
            if source.is_dir():
                shutil.copytree(source, self.root / name, dirs_exist_ok=True)
        generate_board.set_root_dir(self.root)
        generate_board.set_code_dir(self.temp / "code")

    def _write_ticket(self, epic: str, ticket_id: str, **fields) -> Path:
        """Write a minimal ticket JSON under the temporary content/ tree.

        :param epic: epic directory name.
        :param ticket_id: ticket ID, used as the file name.
        :param fields: extra ticket fields merged over the defaults.
        :return: path of the written ticket.
        """
        data = {
            "id": ticket_id,
            "epic": epic,
            "repo": "Ixdar",
            "subsystem": ["automation"],
            "title": f"{ticket_id} title",
            "description": "d",
            "blocked-by": [],
            "blocks": [],
            "status": "IN_PROGRESS",
            "definition-of-done": "1. done",
            "testing-plan": "1. tested",
            "todos": [],
            "unknowns": [],
            "changes-made": [],
            "related-files": [],
            "priority": 3,
        }
        data.update(fields)
        directory = self.root / "content" / epic
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{ticket_id}.json"
        path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
        return path

    def _make_worktree(self, repo: str, name: str) -> Path:
        """Create an empty worktree-shaped directory under the temporary code root.

        :param repo: repository directory name.
        :param name: worktree directory name.
        :return: the created directory.
        """
        path = self.temp / "code" / repo / ".claude" / "worktrees" / name
        path.mkdir(parents=True)
        return path

    # DoD 1: @file text options round-trip shell-hostile characters.

    def test_description_from_file_round_trips_backticks_and_dollars(self) -> None:
        """create --description @file keeps backticks, dollar signs and newlines verbatim."""
        text = "Uses `wt sync` and $HOME and ${VAR}\nsecond line with `backticks`"
        source = self.temp / "description.txt"
        source.write_text(text + "\n", encoding="utf-8")

        code, _, err = _run(
            "create",
            "--epic", "TEST",
            "--repo", "Ixdar",
            "--title", "Round trip",
            "--description", f"@{source}",
            "--subsystem", "automation",
            "--definition-of-done", "1. holds",
            "--testing-plan", "1. tested",
        )
        self.assertEqual(code, 0, err)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["description"], text)

    def test_at_at_escapes_a_literal_leading_at(self) -> None:
        """@@ yields a literal leading @ instead of reading a file."""
        self.assertEqual(generate_board.text_option("@@handle"), "@handle")
        self.assertEqual(generate_board.text_option("plain"), "plain")

    def test_missing_at_file_is_a_clean_error(self) -> None:
        """A @PATH that does not exist reports the path rather than raising."""
        with self.assertRaises(Exception) as caught:
            generate_board.text_option(f"@{self.temp / 'absent.txt'}")
        self.assertIn("absent.txt", str(caught.exception))

    def test_create_from_json_builds_a_whole_ticket(self) -> None:
        """create --from-json reads every field, and command-line options extend its lists."""
        spec = {
            "epic": "TEST",
            "repo": "Ixdar",
            "title": "From JSON",
            "description": "Body with `backticks`.",
            "subsystem": ["automation"],
            "definition-of-done": "1. built from json",
            "testing-plan": "1. tested",
            "todos": ["first todo"],
            "priority": 2,
        }
        spec_path = self.temp / "ticket.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")

        code, _, err = _run("create", "--from-json", str(spec_path), "--todo", "second todo")
        self.assertEqual(code, 0, err)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["title"], "From JSON")
        self.assertEqual(written["description"], "Body with `backticks`.")
        self.assertEqual(written["definition-of-done"], "1. built from json")
        self.assertEqual(written["todos"], ["first todo", "second todo"])
        self.assertEqual(written["priority"], 2)

    def test_create_without_required_fields_reports_them(self) -> None:
        """create names every missing required field instead of an argparse usage dump."""
        code, _, err = _run("create", "--epic", "TEST", "--repo", "Ixdar")
        self.assertEqual(code, 1)
        self.assertIn("--title", err)
        self.assertIn("--definition-of-done", err)

    # DoD 2: resolving todos and unknowns by index.

    def test_resolve_todo_removes_the_named_entry_and_prints_it(self) -> None:
        """update --resolve-todo 2 drops the second todo, prints it, and keeps the others."""
        self._write_ticket("TEST", "TEST-1", todos=["first", "second", "third"])
        code, out, err = _run("update", "TEST-1", "--resolve-todo", "2")
        self.assertEqual(code, 0, err)
        self.assertIn("resolved todo 2: second", out)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["todos"], ["first", "third"])

    def test_resolve_todo_is_repeatable_and_indexes_the_original_list(self) -> None:
        """Two --resolve-todo flags remove both original positions, not shifted ones."""
        self._write_ticket("TEST", "TEST-1", todos=["a", "b", "c", "d"])
        code, out, err = _run("update", "TEST-1", "--resolve-todo", "1", "--resolve-todo", "3")
        self.assertEqual(code, 0, err)
        self.assertIn("resolved todo 1: a", out)
        self.assertIn("resolved todo 3: c", out)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["todos"], ["b", "d"])

    def test_resolve_unknown_removes_the_named_entry(self) -> None:
        """update --resolve-unknown 1 drops the first unknown and prints it."""
        self._write_ticket("TEST", "TEST-1", unknowns=["only question"])
        code, out, err = _run("update", "TEST-1", "--resolve-unknown", "1")
        self.assertEqual(code, 0, err)
        self.assertIn("resolved unknown 1: only question", out)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["unknowns"], [])

    def test_resolve_out_of_range_lists_the_available_entries(self) -> None:
        """An out-of-range index fails without writing and shows the numbered entries."""
        path = self._write_ticket("TEST", "TEST-1", todos=["only"])
        before = path.read_text(encoding="utf-8")
        code, _, err = _run("update", "TEST-1", "--resolve-todo", "5")
        self.assertEqual(code, 1)
        self.assertIn("1. only", err)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    # DoD 3: show resolves content/ versus done/ from any working directory.

    def test_show_finds_a_ticket_under_done(self) -> None:
        """show prints a ticket that has been archived into done/."""
        directory = self.root / "done" / "TEST"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "TEST-7.json").write_text(
            json.dumps({"id": "TEST-7", "title": "Archived", "todos": ["t"], "unknowns": []}),
            encoding="utf-8",
        )
        code, out, err = _run("show", "TEST-7", "--indices")
        self.assertEqual(code, 0, err)
        self.assertIn("Archived", out)
        self.assertIn("1. t", out)

    def test_root_is_discovered_from_the_script_location(self) -> None:
        """The tickets root is found from this file, so no working directory is required."""
        self.assertEqual(generate_board._discover_root_dir(), REPO_ROOT)

    def test_show_reports_a_missing_ticket_cleanly(self) -> None:
        """An unknown ticket ID exits 1 with a message, not a traceback."""
        code, _, err = _run("show", "TEST-999")
        self.assertEqual(code, 1)
        self.assertIn("TEST-999", err)

    # DoD 4: mark done refuses while the ticket's worktree holds unmerged work.

    def test_mark_done_refuses_while_the_worktree_is_dirty(self) -> None:
        """mark done stops when the ticket's worktree has uncommitted files, and points at review."""
        self._write_ticket("TEST", "TEST-1")
        worktree = self._make_worktree("Ixdar", "test-1")
        self._stub_state({worktree: (3, 0)})

        code, _, err = _run("mark", "done", "TEST-1")
        self.assertEqual(code, 1)
        self.assertIn("3 uncommitted file(s)", err)
        self.assertIn("mark review TEST-1", err)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["status"], "IN_PROGRESS")

    def test_mark_done_refuses_while_the_branch_is_ahead_of_main(self) -> None:
        """A clean worktree carrying commits not on the main branch also blocks mark done."""
        self._write_ticket("TEST", "TEST-1")
        worktree = self._make_worktree("ixdar-tickets", "test-1")
        self._stub_state({worktree: (0, 2)})

        code, _, err = _run("mark", "done", "TEST-1")
        self.assertEqual(code, 1)
        self.assertIn("2 commit(s) not on the main branch", err)

    def test_mark_review_succeeds_while_the_worktree_is_dirty(self) -> None:
        """mark review is the sanctioned state for work waiting in a worktree."""
        self._write_ticket("TEST", "TEST-1")
        worktree = self._make_worktree("Ixdar", "test-1")
        self._stub_state({worktree: (3, 1)})

        code, out, err = _run("mark", "review", "TEST-1")
        self.assertEqual(code, 0, err)
        self.assertIn("Marked TEST-1 REVIEW", out)
        written = json.loads((self.root / "content" / "TEST" / "TEST-1.json").read_text())
        self.assertEqual(written["status"], "REVIEW")

    def test_mark_done_succeeds_when_the_worktree_is_clean(self) -> None:
        """A worktree with nothing uncommitted and nothing ahead does not block mark done."""
        self._write_ticket("TEST", "TEST-1")
        worktree = self._make_worktree("ai-workspace", "test-1")
        self._stub_state({worktree: (0, 0)})

        code, out, err = _run("mark", "done", "TEST-1")
        self.assertEqual(code, 0, err)
        self.assertIn("Marked TEST-1 DONE", out)
        self.assertTrue((self.root / "done" / "TEST" / "TEST-1.json").exists())

    def test_mark_done_force_overrides_the_guard(self) -> None:
        """--force marks DONE even with a dirty worktree, for work that has already landed."""
        self._write_ticket("TEST", "TEST-1")
        worktree = self._make_worktree("Ixdar", "test-1")
        self._stub_state({worktree: (5, 0)})

        code, out, err = _run("mark", "done", "TEST-1", "--force")
        self.assertEqual(code, 0, err)
        self.assertIn("Marked TEST-1 DONE", out)

    def test_update_status_done_is_guarded_too(self) -> None:
        """The guard also covers update --status DONE, which is the same act by another route."""
        self._write_ticket("TEST", "TEST-1")
        worktree = self._make_worktree("Ixdar", "test-1")
        self._stub_state({worktree: (1, 0)})

        code, _, err = _run("update", "TEST-1", "--status", "DONE")
        self.assertEqual(code, 1)
        self.assertIn("1 uncommitted file(s)", err)

    def test_worktree_lookup_covers_the_three_known_repos(self) -> None:
        """A worktree named after the lowercased ticket is found in each known repository."""
        self.assertEqual(generate_board.find_ticket_worktrees("TEST-1"), [])
        expected = [self._make_worktree(repo, "test-1") for repo in generate_board.WORKTREE_REPOS]
        self.assertEqual(generate_board.find_ticket_worktrees("TEST-1"), expected)

    def test_worktree_state_of_a_non_git_directory_is_none(self) -> None:
        """A directory that is not a git worktree reports no state and never blocks."""
        plain = self._make_worktree("Ixdar", "test-1")
        self.assertIsNone(generate_board.worktree_state(plain))
        self.assertEqual(generate_board.unmerged_ticket_worktrees("TEST-1"), [])

    def _stub_state(self, states: dict) -> None:
        """Replace worktree_state with a lookup table so tests need no real git repositories.

        :param states: worktree path to (uncommitted count, commits ahead) mapping.
        """
        original = generate_board.worktree_state
        self.addCleanup(setattr, generate_board, "worktree_state", original)
        generate_board.worktree_state = lambda path: states.get(path)

    # DoD 5: help marks repeatable options.

    def test_every_repeatable_option_says_so_in_help(self) -> None:
        """Each append-action option's help ends up carrying the word Repeatable."""
        parser = generate_board._build_parser()
        checked = 0
        for name, subparser in self._subparsers(parser):
            for action in subparser._actions:
                if isinstance(action, argparse._AppendAction):
                    checked += 1
                    self.assertIn("Repeatable", action.help or "", f"{name} {action.option_strings}")
        self.assertGreater(checked, 8)

    def test_every_at_file_option_says_so_in_help(self) -> None:
        """Each option that resolves @PATH advertises it, and the epilog explains the syntax."""
        parser = generate_board._build_parser()
        for name, subparser in self._subparsers(parser):
            for action in subparser._actions:
                if action.type is generate_board.text_option:
                    self.assertIn("@FILE", action.help or "", f"{name} {action.option_strings}")
        self.assertIn("Repeatable", parser.epilog)
        self.assertIn("@FILE", parser.epilog)

    def _subparsers(self, parser) -> list:
        """Return (name, parser) for a parser and every subparser under it.

        :param parser: root parser to walk.
        :return: flat list of named parsers.
        """
        found = [("tix", parser)]
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, subparser in action.choices.items():
                    found.extend(
                        (f"{name} {inner}" if inner != "tix" else name, sub)
                        for inner, sub in self._subparsers(subparser)
                    )
        return found

    # DoD 6: the board still regenerates.

    def test_board_regenerates_after_an_update(self) -> None:
        """Any mutation rewrites BOARD.md and reports the new counts."""
        self._write_ticket("TEST", "TEST-1", status="TODO")
        code, out, err = _run("update", "TEST-1", "--add-changes", "did a thing")
        self.assertEqual(code, 0, err)
        self.assertIn("Board updated:", out)
        board = (self.root / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("TEST-1", board)

    def test_board_command_regenerates_the_whole_board(self) -> None:
        """tix board rewrites BOARD.md from the ticket JSONs alone."""
        self._write_ticket("TEST", "TEST-1", status="REVIEW")
        code, out, err = _run("board")
        self.assertEqual(code, 0, err)
        self.assertIn("Board updated:", out)
        self.assertIn("### Review", (self.root / "BOARD.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
