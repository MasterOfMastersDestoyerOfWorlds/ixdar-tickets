"""Generate BOARD.md, inspect the board, and create or patch ticket JSONs.

Installed as the `tix` command (uv tool install --editable ixdar-tickets); the same
subcommands work as `python ixdar-tickets/generate_board.py ...`. Either way the tickets
repo is located from this file, so every subcommand works from any working directory.

Usage:
    tix board                        # regenerate BOARD.md
    tix backlog REPO_NAME            # print tickets for a repo
    tix archive                      # move DONE tickets to done/
    tix priorities --epic PATCH      # list tickets by priority
    tix next-id EPIC                 # print next ticket ID for epic
    tix show TICKET_ID               # print a ticket, from content/ or done/
    tix create ...                   # new ticket JSON + board
    tix create --from-json FILE      # new ticket from a JSON file
    tix mark done TICKET_ID          # mark a ticket DONE and regenerate BOARD.md
    tix update TICKET_ID --status IN_PROGRESS --append-description "..."
    tix update TICKET_ID --resolve-todo 2 --resolve-unknown 1

Every free-text option also accepts @PATH, which reads the value from a file (and @- reads
stdin), so text containing backticks, dollar signs or newlines never passes through the
shell. Write a literal leading @ as @@.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _discover_root_dir() -> Path:
    """Return the tickets repo root: IXDAR_TICKETS_ROOT, else the nearest ancestor holding content/."""
    override = os.environ.get("IXDAR_TICKETS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "content").is_dir() and (candidate / "generate_board.py").is_file():
            return candidate
    return here


ROOT_DIR = _discover_root_dir()
TICKETS_DIR = ROOT_DIR / "content"
DONE_DIR = ROOT_DIR / "done"
SUGGESTIONS_DIR = ROOT_DIR / "suggestions"
BOARD_PATH = ROOT_DIR / "BOARD.md"
EPICS_PATH = TICKETS_DIR / "epics.json"


def set_root_dir(root: Path | str) -> None:
    """Point every store path at a different tickets root; used by tests and IXDAR_TICKETS_ROOT.

    :param root: directory holding content/, done/ and BOARD.md.
    """
    global ROOT_DIR, TICKETS_DIR, DONE_DIR, SUGGESTIONS_DIR, BOARD_PATH, EPICS_PATH
    ROOT_DIR = Path(root).expanduser().resolve()
    TICKETS_DIR = ROOT_DIR / "content"
    DONE_DIR = ROOT_DIR / "done"
    SUGGESTIONS_DIR = ROOT_DIR / "suggestions"
    BOARD_PATH = ROOT_DIR / "BOARD.md"
    EPICS_PATH = TICKETS_DIR / "epics.json"


# Epics whose tickets live outside content/ (i.e. excluded from BOARD.md and
# all summary counts). These are agent-generated logs, not work to be done.
EXTERNAL_EPICS = {"SUGGEST"}

STATUS_ORDER = {"PINNED": 0, "REVIEW": 1, "IN_PROGRESS": 2, "TODO": 3, "DONE": 4}
DESC_TRUNCATE = 100

# Repositories whose .claude/worktrees/<ticket-id-lowercased> directory is checked before a
# ticket may go DONE, and the directory holding them (e.g. /home/acw/Code).
WORKTREE_REPOS = ("Ixdar", "ai-workspace", "ixdar-tickets")
MAIN_BRANCH_NAMES = ("main", "master")


def _discover_code_dir() -> Path:
    """Return the directory holding the known repositories, walking up from the tickets root.

    Walking up matters because the script may itself be running from a checkout inside
    .claude/worktrees/, where the tickets root's parent is not the code directory.

    :return: the nearest ancestor containing one of WORKTREE_REPOS, else the root's parent.
    """
    override = os.environ.get("IXDAR_CODE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    for candidate in (ROOT_DIR, *ROOT_DIR.parents):
        if any((candidate / repo).is_dir() for repo in WORKTREE_REPOS):
            return candidate
    return ROOT_DIR.parent


CODE_DIR = _discover_code_dir()


def set_code_dir(code_dir: Path | str) -> None:
    """Point the mark-done worktree guard at a different parent-of-repos directory.

    :param code_dir: directory holding the repositories named in WORKTREE_REPOS.
    """
    global CODE_DIR
    CODE_DIR = Path(code_dir).expanduser().resolve()


def text_option(raw: str) -> str:
    """Resolve one free-text option value: @PATH reads a file, @- reads stdin, @@ escapes a literal @.

    :param raw: the value as typed on the command line.
    :return: the literal text, with at most one trailing newline stripped.
    """
    if raw.startswith("@@"):
        return raw[1:]
    if not raw.startswith("@"):
        return raw
    source = raw[1:]
    if source == "-":
        text = sys.stdin.read()
    else:
        path = Path(source).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise argparse.ArgumentTypeError(f"cannot read {path}: {exc}") from exc
    if text.endswith("\n"):
        text = text[:-1]
    return text


def load_epics() -> tuple[dict[str, str], dict[str, int]]:
    """Return (prefix -> name, prefix -> priority) mappings."""
    if not EPICS_PATH.exists():
        return {}, {}
    with EPICS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    names = {}
    priorities = {}
    for e in data.get("epics", []):
        names[e["prefix"]] = e["name"]
        priorities[e["prefix"]] = e.get("priority", 50)
    return names, priorities


def normalize_status(raw: str | None) -> str:
    if not raw:
        return "TODO"
    upper = raw.strip().upper()
    if upper in ("DONE", "COMPLETE", "COMPLETED"):
        return "DONE"
    if upper in ("IN_PROGRESS", "IN-PROGRESS", "WIP"):
        return "IN_PROGRESS"
    if upper in ("REVIEW", "IN_REVIEW", "IN-REVIEW", "READY"):
        return "REVIEW"
    if upper in ("PINNED", "PIN", "GOAL"):
        return "PINNED"
    if upper in ("TODO", "OPEN", "BACKLOG"):
        return "TODO"
    return upper


def extract_prefix(ticket_id: str) -> str:
    match = re.match(r"^([A-Z]+)-", ticket_id)
    return match.group(1) if match else ""


def extract_number(ticket_id: str) -> int:
    match = re.search(r"-(\d+)$", ticket_id)
    return int(match.group(1)) if match else 0


def truncate(text: str, limit: int = DESC_TRUNCATE) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def load_tickets() -> list[dict]:
    tickets = []
    for category_dir in sorted(TICKETS_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for ticket_file in sorted(category_dir.glob("*.json")):
            try:
                with ticket_file.open(encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content.startswith("{"):
                        continue
                    data = json.loads(content)
            except (json.JSONDecodeError, OSError):
                continue
            ticket_id = data.get("id", ticket_file.stem)
            rel_path = ticket_file.relative_to(TICKETS_DIR.parent)
            tickets.append({
                "id": ticket_id,
                "title": data.get("title", ticket_id),
                "status": normalize_status(data.get("status")),
                "description": data.get("description", ""),
                "prefix": extract_prefix(ticket_id),
                "number": extract_number(ticket_id),
                "priority": data.get("priority", extract_number(ticket_id)),
                "rel_path": str(rel_path).replace("\\", "/"),
                "repo": data.get("repo", ""),
                "blocked_by": data.get("blocked-by", []),
                "blocks": data.get("blocks", []),
            })
    return tickets


def group_by_epic(
    tickets: list[dict],
) -> dict[str, dict[str, list[dict]]]:
    """Group tickets: epic prefix -> status -> sorted ticket list."""
    grouped: dict[str, dict[str, list[dict]]] = {}
    for t in tickets:
        prefix = t["prefix"]
        status = t["status"]
        grouped.setdefault(prefix, {}).setdefault(status, []).append(t)
    for epic_group in grouped.values():
        for ticket_list in epic_group.values():
            ticket_list.sort(key=lambda t: (t["priority"], t["number"]))
    return grouped


def render_ticket_line(t: dict, done: bool = False) -> str:
    if done:
        return (
            f"- ~~[{t['id']}]({t['rel_path']}) "
            f"**{t['id']}** {t['title']}~~"
        )
    desc = truncate(t["description"])
    desc_part = f" -- {desc}" if desc else ""
    return (
        f"- [{t['id']}]({t['rel_path']}) "
        f"**{t['id']}** {t['title']}{desc_part}"
    )


def render_board(
    grouped: dict[str, dict[str, list[dict]]],
    epic_names: dict[str, str],
    epic_priorities: dict[str, int],
) -> str:
    lines = [
        "# Ticket Board",
        "*Auto-generated by generate_board.py -- do not edit manually*",
        "",
    ]

    # Pinned goal tickets render at the very top, across all epics, so every
    # session sees the destination set first thing. Pinned status is for
    # end-goal/maximally-blocked tickets that don't churn — not for "next
    # thing to work on".
    pinned: list[dict] = []
    for status_groups in grouped.values():
        pinned.extend(status_groups.get("PINNED", []))
    if pinned:
        pinned.sort(key=lambda t: (t["priority"], t["prefix"], t["number"]))
        lines.append(f"## 📌 Goals ({len(pinned)} pinned)")
        lines.append("")
        for t in pinned:
            lines.append(render_ticket_line(t))
        lines.append("")
        lines.append("---")
        lines.append("")

    sorted_prefixes = sorted(
        grouped.keys(),
        key=lambda p: epic_priorities.get(p, 50),
    )

    for prefix in sorted_prefixes:
        status_groups = grouped[prefix]
        epic_name = epic_names.get(prefix, prefix)
        # Skip the pinned bucket — already rendered at top.
        non_pinned_total = sum(
            len(v) for k, v in status_groups.items() if k != "PINNED"
        )
        if non_pinned_total == 0:
            continue
        epic_prio = epic_priorities.get(prefix, 50)
        lines.append(f"## {epic_name} ({non_pinned_total} tickets, priority {epic_prio})")
        lines.append("")

        # REVIEW: implemented in a worktree, waiting for the user's verification and merge
        review = status_groups.get("REVIEW", [])
        if review:
            lines.append(f"### Review ({len(review)})")
            lines.append("")
            for t in review:
                lines.append(render_ticket_line(t))
            lines.append("")

        # IN_PROGRESS
        in_progress = status_groups.get("IN_PROGRESS", [])
        if in_progress:
            lines.append(f"### In Progress ({len(in_progress)})")
            lines.append("")
            for t in in_progress:
                lines.append(render_ticket_line(t))
            lines.append("")

        # TODO
        todo = status_groups.get("TODO", [])
        if todo:
            lines.append(f"### Todo ({len(todo)})")
            lines.append("")
            for t in todo:
                lines.append(render_ticket_line(t))
            lines.append("")

        # DONE (collapsed)
        done = status_groups.get("DONE", [])
        if done:
            lines.append(f"<details><summary>Done ({len(done)})</summary>")
            lines.append("")
            for t in done:
                lines.append(render_ticket_line(t, done=True))
            lines.append("")
            lines.append("</details>")
            lines.append("")

        # Any non-standard statuses
        for status in sorted(status_groups.keys()):
            if status in STATUS_ORDER:
                continue
            other = status_groups[status]
            lines.append(f"### {status} ({len(other)})")
            lines.append("")
            for t in other:
                lines.append(render_ticket_line(t))
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def print_repo_tickets(repo: str) -> None:
    """Print a compact ticket summary filtered to a single repo.

    Groups by epic, shows actionable tickets (IN_PROGRESS, TODO) with
    priorities and dependency info, and a count of DONE tickets.
    """
    epic_names, epic_priorities = load_epics()
    all_tickets = load_tickets()
    repo_tickets = [t for t in all_tickets if t["repo"] == repo]

    if not repo_tickets:
        print(f"No tickets found for repo '{repo}'.")
        print(f"Available repos: {sorted({t['repo'] for t in all_tickets if t['repo']})}")
        return

    grouped = group_by_epic(repo_tickets)
    sorted_prefixes = sorted(
        grouped.keys(),
        key=lambda p: epic_priorities.get(p, 50),
    )

    for prefix in sorted_prefixes:
        status_groups = grouped[prefix]
        epic_name = epic_names.get(prefix, prefix)
        actionable = (len(status_groups.get("REVIEW", [])) + len(status_groups.get("IN_PROGRESS", []))
                      + len(status_groups.get("TODO", [])))
        done_count = len(status_groups.get("DONE", []))
        print(f"\n{epic_name} ({prefix}) -- {actionable} actionable, {done_count} done")
        print("=" * 60)

        for status in ("REVIEW", "IN_PROGRESS", "TODO"):
            tickets_in_status = status_groups.get(status, [])
            if not tickets_in_status:
                continue
            label = {"REVIEW": "REVIEW", "IN_PROGRESS": "IN PROGRESS"}.get(status, "TODO")
            print(f"\n  {label}:")
            for t in tickets_in_status:
                deps = ""
                if t["blocked_by"]:
                    deps = f"  (blocked-by: {', '.join(t['blocked_by'])})"
                if t["blocks"]:
                    deps += f"  (blocks: {', '.join(t['blocks'])})"
                desc = truncate(t["description"], 80)
                print(f"    {t['id']} [P{t['priority']}] {t['title']}{deps}")
                if desc:
                    print(f"      {desc}")

        if done_count:
            print(f"\n  DONE: {done_count} tickets (use BOARD.md for details)")


def _scan_ticket_files(*dirs: Path) -> list[Path]:
    """Glob all ticket JSONs across multiple directories."""
    files = []
    for d in dirs:
        if not d.exists():
            continue
        for epic_dir in sorted(d.iterdir()):
            if epic_dir.is_dir():
                files.extend(sorted(epic_dir.glob("*.json")))
    return files


def archive_done() -> None:
    """Move all DONE tickets from content/ to done/, preserving epic subdirectories."""
    moved = []
    for epic_dir in sorted(TICKETS_DIR.iterdir()):
        if not epic_dir.is_dir():
            continue
        for ticket_file in sorted(epic_dir.glob("*.json")):
            try:
                data = json.loads(ticket_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if normalize_status(data.get("status")) != "DONE":
                continue

            dest_dir = DONE_DIR / epic_dir.name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / ticket_file.name
            shutil.move(str(ticket_file), str(dest))
            moved.append(f"{epic_dir.name}/{ticket_file.name}")

    if moved:
        print(f"Archived {len(moved)} done ticket(s) to done/:")
        for name in moved:
            print(f"  {name}")
    else:
        print("No DONE tickets to archive.")


def _next_ticket_id(epic: str) -> str:
    """Return the next ticket id (e.g. IX-10) for an epic across all stores."""
    prefix = epic.upper()
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.json$")

    max_num = 0
    for ticket_file in _scan_ticket_files(TICKETS_DIR, DONE_DIR, SUGGESTIONS_DIR):
        match = pattern.match(ticket_file.name)
        if match:
            max_num = max(max_num, int(match.group(1)))

    return f"{prefix}-{max_num + 1}"


def print_next_id(epic: str) -> None:
    """Find the highest ticket number for an epic across content/ and done/, print the next one."""
    print(_next_ticket_id(epic))


# create fields that hold a single string, mapped from both JSON spellings.
CREATE_TEXT_FIELDS = {
    "epic": ("epic",),
    "repo": ("repo",),
    "title": ("title",),
    "description": ("description",),
    "definition_of_done": ("definition-of-done", "definition_of_done"),
    "testing_plan": ("testing-plan", "testing_plan"),
}

# create fields that hold a list of strings, mapped from both JSON spellings.
CREATE_LIST_FIELDS = {
    "subsystem": ("subsystem", "subsystems"),
    "todos": ("todos", "todo"),
    "unknowns": ("unknowns", "unknown"),
    "related_files": ("related-files", "related_files"),
    "blocked_by": ("blocked-by", "blocked_by"),
    "blocks": ("blocks",),
}

REQUIRED_CREATE_FIELDS = (
    ("epic", "--epic"),
    ("repo", "--repo"),
    ("title", "--title"),
    ("description", "--description"),
    ("subsystem", "--subsystem"),
    ("definition_of_done", "--definition-of-done"),
    ("testing_plan", "--testing-plan"),
)


def create_fields_from_json(path: Path | str) -> dict:
    """Read a whole ticket from a JSON file into create_ticket keyword arguments.

    :param path: JSON file holding one ticket object; "-" reads stdin.
    :return: keyword arguments for create_ticket, omitting absent fields.
    """
    source = Path(path).expanduser()
    text = sys.stdin.read() if str(path) == "-" else source.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("--from-json expects a JSON object holding one ticket.")
    fields: dict = {}
    for name, spellings in CREATE_TEXT_FIELDS.items():
        for spelling in spellings:
            if raw.get(spelling) is not None:
                fields[name] = str(raw[spelling])
                break
    for name, spellings in CREATE_LIST_FIELDS.items():
        for spelling in spellings:
            value = raw.get(spelling)
            if value is None:
                continue
            fields[name] = [str(item) for item in value] if isinstance(value, list) else [str(value)]
            break
    if raw.get("priority") is not None:
        fields["priority"] = int(raw["priority"])
    return fields


def create_ticket(
    epic: str,
    repo: str,
    title: str,
    description: str,
    subsystem: list[str],
    priority: int,
    definition_of_done: str,
    testing_plan: str,
    todos: list[str],
    blocked_by: list[str] | None = None,
    blocks: list[str] | None = None,
    unknowns: list[str] | None = None,
    related_files: list[str] | None = None,
) -> Path:
    """Write a new ticket JSON under content/<EPIC>/ and regenerate BOARD.md.

    External epics (e.g. SUGGEST — agent harness output) are written under
    suggestions/<EPIC>/ instead and excluded from BOARD.md totals.
    Dependency links are written reciprocally onto the referenced tickets.
    """
    ticket_id = _next_ticket_id(epic)
    prefix = extract_prefix(ticket_id)
    epic_root = SUGGESTIONS_DIR if prefix in EXTERNAL_EPICS else TICKETS_DIR
    epic_dir = epic_root / prefix
    epic_dir.mkdir(parents=True, exist_ok=True)
    ticket_path = epic_dir / f"{ticket_id}.json"
    if ticket_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing ticket: {ticket_path}")

    data = {
        "id": ticket_id,
        "epic": prefix,
        "repo": repo,
        "subsystem": list(subsystem),
        "title": title,
        "description": description,
        "blocked-by": _normalized_ids(blocked_by),
        "blocks": _normalized_ids(blocks),
        "status": "TODO",
        "definition-of-done": definition_of_done,
        "testing-plan": testing_plan,
        "todos": todos,
        "unknowns": list(unknowns or []),
        "changes-made": [],
        "related-files": list(related_files or []),
        "priority": priority,
    }
    ticket_path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    _link_dependencies(ticket_id, data["blocked-by"], data["blocks"])
    regenerate_board()
    return ticket_path


def _write_board() -> tuple[int, int, int, int]:
    epic_names, epic_priorities = load_epics()
    tickets = load_tickets()
    grouped = group_by_epic(tickets)
    board = render_board(grouped, epic_names, epic_priorities)
    BOARD_PATH.write_text(board, encoding="utf-8")

    total_pinned = sum(len(g.get("PINNED", [])) for g in grouped.values())
    total_review = sum(len(g.get("REVIEW", [])) for g in grouped.values())
    total_ip = sum(len(g.get("IN_PROGRESS", [])) for g in grouped.values())
    total_todo = sum(len(g.get("TODO", [])) for g in grouped.values())
    total_done = sum(len(g.get("DONE", [])) for g in grouped.values())
    return total_pinned, total_review, total_ip, total_todo, total_done


def _board_summary_line(totals: tuple[int, int, int, int, int]) -> str:
    pinned, review, ip, todo, done = totals
    parts = []
    if pinned:
        parts.append(f"{pinned} pinned")
    if review:
        parts.append(f"{review} review")
    parts.extend([f"{ip} in-progress", f"{todo} todo", f"{done} done"])
    return "Board updated: " + ", ".join(parts)


def regenerate_board() -> None:
    totals = _write_board()
    print(_board_summary_line(totals))


def _ticket_path(ticket_id: str) -> Path:
    normalized_id = ticket_id.strip().upper()
    prefix = extract_prefix(normalized_id)
    number = extract_number(normalized_id)
    if not prefix or number <= 0:
        raise ValueError(f"Invalid ticket ID '{ticket_id}'. Expected format like IX-3.")

    for base in (TICKETS_DIR, DONE_DIR, SUGGESTIONS_DIR):
        candidate = base / prefix / f"{normalized_id}.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Ticket not found: {normalized_id}")


def _move_ticket_to(ticket_path: Path, target_base: Path) -> Path:
    """Move a ticket JSON to target_base/<EPIC>/. No-op if already there."""
    epic_name = ticket_path.parent.name
    dest_dir = target_base / epic_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ticket_path.name
    if ticket_path == dest:
        return ticket_path
    shutil.move(str(ticket_path), str(dest))
    return dest


def _normalize_done_todos(todos: list[str]) -> list[str]:
    normalized = []
    for todo in todos:
        text = str(todo).strip()
        if not text:
            continue
        if text.startswith("DONE : "):
            normalized.append(text)
            continue
        if text.startswith("DONE:"):
            normalized.append(f"DONE : {text[len('DONE:'):].strip()}")
            continue
        normalized.append(f"DONE : {text}")
    return normalized


class UnmergedWorktreeError(Exception):
    """Raised when a ticket is marked DONE while its worktree still holds unmerged work."""


def _git(worktree: Path, *args: str) -> str | None:
    """Run one git command in a worktree, returning its stdout or None when it fails.

    :param worktree: directory to run git in.
    :param args: git arguments after the directory selection.
    :return: stdout text, or None if git is missing or exited non-zero.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def worktree_state(worktree: Path) -> tuple[int, int] | None:
    """Return (uncommitted file count, commits not on the main branch) for a worktree.

    :param worktree: worktree directory to inspect.
    :return: the two counts, or None when the directory is not a usable git worktree.
    """
    status = _git(worktree, "status", "--porcelain")
    if status is None:
        return None
    uncommitted = len([line for line in status.splitlines() if line.strip()])
    ahead = 0
    for branch in MAIN_BRANCH_NAMES:
        if _git(worktree, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is None:
            continue
        counted = _git(worktree, "rev-list", "--count", f"{branch}..HEAD")
        if counted is not None and counted.strip().isdigit():
            ahead = int(counted.strip())
        break
    return uncommitted, ahead


def find_ticket_worktrees(ticket_id: str) -> list[Path]:
    """Return existing worktrees named after a ticket across the known repositories.

    :param ticket_id: ticket ID whose lowercased form names the worktree directory.
    :return: worktree directories that exist, in WORKTREE_REPOS order.
    """
    name = ticket_id.strip().lower()
    found = []
    for repo in WORKTREE_REPOS:
        candidate = CODE_DIR / repo / ".claude" / "worktrees" / name
        if candidate.is_dir():
            found.append(candidate)
    return found


def unmerged_ticket_worktrees(ticket_id: str) -> list[tuple[Path, int, int]]:
    """Return the ticket's worktrees that still hold unmerged work, with their counts.

    :param ticket_id: ticket ID to look up.
    :return: (worktree, uncommitted file count, commits not on main) for each unmerged worktree.
    """
    unmerged = []
    for worktree in find_ticket_worktrees(ticket_id):
        state = worktree_state(worktree)
        if state is None:
            continue
        uncommitted, ahead = state
        if uncommitted or ahead:
            unmerged.append((worktree, uncommitted, ahead))
    return unmerged


def _check_worktrees_merged(ticket_id: str) -> None:
    """Raise UnmergedWorktreeError when a worktree named after the ticket is dirty or ahead.

    :param ticket_id: ticket ID about to be marked DONE.
    """
    unmerged = unmerged_ticket_worktrees(ticket_id)
    if not unmerged:
        return
    lines = [f"Refusing to mark {ticket_id} DONE: its worktree still holds unmerged work."]
    for worktree, uncommitted, ahead in unmerged:
        detail = []
        if uncommitted:
            detail.append(f"{uncommitted} uncommitted file(s)")
        if ahead:
            detail.append(f"{ahead} commit(s) not on the main branch")
        lines.append(f"  {worktree}: {', '.join(detail)}")
    lines.append(f"Mark it REVIEW instead: mark review {ticket_id}")
    lines.append("Pass --force to mark it DONE anyway once the work has landed.")
    raise UnmergedWorktreeError("\n".join(lines))


def mark_ticket_done(ticket_id: str, force: bool = False) -> None:
    """Mark a ticket DONE, normalize todo prefixes, archive into done/, regen board.

    :param ticket_id: ticket ID to mark done.
    :param force: skip the unmerged-worktree guard.
    """
    ticket_path = _ticket_path(ticket_id)
    data = json.loads(ticket_path.read_text(encoding="utf-8"))
    normalized_id = data.get("id", ticket_id.strip().upper())
    if not force:
        _check_worktrees_merged(normalized_id)

    data["status"] = "DONE"
    if isinstance(data.get("todos"), list):
        data["todos"] = _normalize_done_todos(data["todos"])

    ticket_path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    ticket_path = _move_ticket_to(ticket_path, DONE_DIR)
    totals = _write_board()
    print(f"Marked {normalized_id} DONE -> {ticket_path.relative_to(ROOT_DIR)}")
    print(_board_summary_line(totals))


def mark_ticket_status(ticket_id: str, status: str, force: bool = False) -> None:
    """Set status, auto-(un)archive based on DONE state, regen board.

    :param ticket_id: ticket ID to change.
    :param status: new status, normalized before it is stored.
    :param force: skip the unmerged-worktree guard when the new status is DONE.
    """
    normalized_status = normalize_status(status)
    ticket_path = _ticket_path(ticket_id)
    data = json.loads(ticket_path.read_text(encoding="utf-8"))
    normalized_id = data.get("id", ticket_id.strip().upper())
    if normalized_status == "DONE" and not force:
        _check_worktrees_merged(normalized_id)

    data["status"] = normalized_status
    if normalized_status == "DONE" and isinstance(data.get("todos"), list):
        data["todos"] = _normalize_done_todos(data["todos"])

    ticket_path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    if normalized_status == "DONE":
        ticket_path = _move_ticket_to(ticket_path, DONE_DIR)
    else:
        # PINNED, REVIEW, IN_PROGRESS, TODO all live under content/.
        ticket_path = _move_ticket_to(ticket_path, TICKETS_DIR)
    totals = _write_board()
    print(f"Marked {normalized_id} {normalized_status} -> {ticket_path.relative_to(ROOT_DIR)}")
    print(_board_summary_line(totals))


def show_ticket(ticket_id: str, indices: bool = False) -> None:
    """Print a ticket JSON from content/ or done/, optionally with resolvable entry numbers.

    :param ticket_id: ticket ID to print.
    :param indices: also print numbered todos and unknowns for --resolve-todo/--resolve-unknown.
    """
    ticket_path = _ticket_path(ticket_id)
    text = ticket_path.read_text(encoding="utf-8")
    print(text)
    if not indices:
        return
    data = json.loads(text)
    for key in ("todos", "unknowns"):
        entries = data.get(key) or []
        print(f"{key} ({len(entries)}):")
        for number, entry in enumerate(entries, start=1):
            print(f"  {number}. {entry}")


def _append_text_field(data: dict, key: str, fragment: str) -> None:
    fragment = (fragment or "").strip()
    if not fragment:
        return
    existing = (data.get(key) or "").rstrip()
    if existing:
        data[key] = f"{existing}\n\n{fragment}\n"
    else:
        data[key] = f"{fragment}\n"


def _extend_list_field(data: dict, key: str, items: list[str]) -> None:
    if not items:
        return
    current = data.get(key)
    if not isinstance(current, list):
        current = []
    for item in items:
        text = (item or "").strip()
        if text:
            current.append(text)
    data[key] = current


def _resolve_list_entries(data: dict, key: str, indices: list[int]) -> list[str]:
    """Remove one-based entries from a ticket list field and return the removed texts.

    :param data: ticket JSON mapping to mutate.
    :param key: list field name, "todos" or "unknowns".
    :param indices: one-based positions to remove, in any order.
    :return: removed entry texts in the order the indices were given.
    """
    if not indices:
        return []
    current = data.get(key)
    if not isinstance(current, list):
        current = []
    for index in indices:
        if index < 1 or index > len(current):
            listing = "\n".join(f"  {n}. {text}" for n, text in enumerate(current, start=1))
            available = listing or "  (none)"
            raise IndexError(
                f"No {key} entry {index}; the ticket has {len(current)}:\n{available}"
            )
    removed = [str(current[index - 1]) for index in indices]
    keep = {index - 1 for index in indices}
    data[key] = [item for position, item in enumerate(current) if position not in keep]
    return removed


def _normalized_ids(ids: list[str] | None) -> list[str]:
    seen: list[str] = []
    for raw in ids or []:
        for token in str(raw).replace(",", " ").split():
            normalized = token.strip().upper()
            if normalized and normalized not in seen:
                seen.append(normalized)
    return seen


def _add_link(target_id: str, key: str, value_id: str) -> None:
    """Append value_id to target's key list ("blocks" or "blocked-by") if the target exists in content/."""
    try:
        target_path = _ticket_path(target_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"  note: not linking {target_id}.{key} -> {value_id}: {exc}")
        return
    target = json.loads(target_path.read_text(encoding="utf-8"))
    current = target.get(key)
    if not isinstance(current, list):
        current = []
    if value_id not in current:
        current.append(value_id)
        target[key] = current
        target_path.write_text(json.dumps(target, indent=4) + "\n", encoding="utf-8")


def _link_dependencies(ticket_id: str, blocked_by: list[str], blocks: list[str]) -> None:
    """Mirror a ticket's blocked-by/blocks onto the referenced tickets so links stay reciprocal."""
    for other in blocked_by:
        _add_link(other, "blocks", ticket_id)
    for other in blocks:
        _add_link(other, "blocked-by", ticket_id)


def update_ticket(
    ticket_id: str,
    *,
    add_blocked_by: list[str] | None = None,
    add_blocks: list[str] | None = None,
    append_description: str | None = None,
    definition_of_done: str | None = None,
    testing_plan: str | None = None,
    append_definition_of_done: str | None = None,
    append_testing_plan: str | None = None,
    add_unknowns: list[str] | None = None,
    add_changes: list[str] | None = None,
    add_todos: list[str] | None = None,
    add_related_files: list[str] | None = None,
    resolve_todos: list[int] | None = None,
    resolve_unknowns: list[int] | None = None,
    status: str | None = None,
    force: bool = False,
) -> Path:
    """Patch an existing ticket JSON and regenerate BOARD.md.

    :param ticket_id: ticket ID to patch.
    :param resolve_todos: one-based todo positions to remove.
    :param resolve_unknowns: one-based unknown positions to remove.
    :param force: skip the unmerged-worktree guard when the new status is DONE.
    :return: path of the patched ticket JSON.
    """
    ticket_path = _ticket_path(ticket_id)
    data = json.loads(ticket_path.read_text(encoding="utf-8"))
    normalized_id = data.get("id", ticket_id.strip().upper())
    if status is not None and normalize_status(status) == "DONE" and not force:
        _check_worktrees_merged(normalized_id)

    removed_todos = _resolve_list_entries(data, "todos", resolve_todos or [])
    removed_unknowns = _resolve_list_entries(data, "unknowns", resolve_unknowns or [])

    if append_description:
        _append_text_field(data, "description", append_description)
    if definition_of_done is not None:
        data["definition-of-done"] = definition_of_done.strip() + "\n"
    elif append_definition_of_done:
        _append_text_field(data, "definition-of-done", append_definition_of_done)
    if testing_plan is not None:
        data["testing-plan"] = testing_plan.strip() + "\n"
    elif append_testing_plan:
        _append_text_field(data, "testing-plan", append_testing_plan)

    _extend_list_field(data, "unknowns", add_unknowns or [])
    _extend_list_field(data, "changes-made", add_changes or [])
    _extend_list_field(data, "related-files", add_related_files or [])

    new_blocked_by = [i for i in _normalized_ids(add_blocked_by) if i not in (data.get("blocked-by") or [])]
    new_blocks = [i for i in _normalized_ids(add_blocks) if i not in (data.get("blocks") or [])]
    _extend_list_field(data, "blocked-by", new_blocked_by)
    _extend_list_field(data, "blocks", new_blocks)
    _link_dependencies(normalized_id, new_blocked_by, new_blocks)

    if add_todos:
        todos = data.get("todos")
        if not isinstance(todos, list):
            todos = []
        for item in add_todos:
            text = (item or "").strip()
            if text:
                todos.append(text)
        data["todos"] = todos

    if status is not None:
        normalized_status = normalize_status(status)
        data["status"] = normalized_status
        if normalized_status == "DONE" and isinstance(data.get("todos"), list):
            data["todos"] = _normalize_done_todos(data["todos"])

    ticket_path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    totals = _write_board()
    print(f"Updated {normalized_id}")
    for index, text in zip(resolve_todos or [], removed_todos):
        print(f"  resolved todo {index}: {text}")
    for index, text in zip(resolve_unknowns or [], removed_unknowns):
        print(f"  resolved unknown {index}: {text}")
    if removed_todos:
        print(f"  todos remaining: {len(data.get('todos') or [])}")
    if removed_unknowns:
        print(f"  unknowns remaining: {len(data.get('unknowns') or [])}")
    print(_board_summary_line(totals))
    return ticket_path


def _load_ticket_records(include_done: bool) -> list[dict]:
    """Return a flat list of {id, status, priority, title, prefix, number, rel_path}."""
    bases = [TICKETS_DIR]
    if include_done:
        bases.append(DONE_DIR)
    out = []
    for ticket_file in _scan_ticket_files(*bases):
        try:
            data = json.loads(ticket_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ticket_id = data.get("id", ticket_file.stem)
        out.append({
            "id": ticket_id,
            "status": normalize_status(data.get("status")),
            "priority": data.get("priority", 50),
            "title": data.get("title", ticket_id),
            "prefix": extract_prefix(ticket_id),
            "number": extract_number(ticket_id),
            "rel_path": str(ticket_file.relative_to(ROOT_DIR)).replace("\\", "/"),
        })
    return out


def print_priorities(epic: str | None, status_filter: str | None, include_done: bool) -> None:
    """Print tickets grouped by priority, sorted by (priority, status, number).

    Defaults: hides DONE tickets and shows all epics. Use --all to include DONE.
    """
    tickets = _load_ticket_records(include_done=include_done or status_filter == "DONE")
    if epic:
        prefix = epic.strip().upper()
        tickets = [t for t in tickets if t["prefix"] == prefix]
    if status_filter:
        normalized = normalize_status(status_filter)
        tickets = [t for t in tickets if t["status"] == normalized]
    elif not include_done:
        tickets = [t for t in tickets if t["status"] != "DONE"]

    if not tickets:
        print("(no tickets match)")
        return

    by_epic: dict[str, list[dict]] = {}
    for t in tickets:
        by_epic.setdefault(t["prefix"], []).append(t)

    for prefix in sorted(by_epic.keys()):
        bucket = by_epic[prefix]
        review = sum(1 for t in bucket if t["status"] == "REVIEW")
        ip = sum(1 for t in bucket if t["status"] == "IN_PROGRESS")
        todo = sum(1 for t in bucket if t["status"] == "TODO")
        done = sum(1 for t in bucket if t["status"] == "DONE")
        header = f"{prefix} ({len(bucket)} total"
        parts = []
        if review:
            parts.append(f"{review} review")
        if ip:
            parts.append(f"{ip} in-progress")
        if todo:
            parts.append(f"{todo} todo")
        if done:
            parts.append(f"{done} done")
        if parts:
            header += ": " + ", ".join(parts)
        header += ")"
        print(header)
        bucket.sort(key=lambda t: (t["priority"], STATUS_ORDER.get(t["status"], 99), t["number"]))
        last_prio = None
        for t in bucket:
            if t["priority"] != last_prio:
                print(f"  P{t['priority']}")
                last_prio = t["priority"]
            title = t["title"]
            if len(title) > 80:
                title = title[:77] + "..."
            print(f"    {t['id']:<10} {t['status']:<12} {title}")
        print()


HELP_EPILOG = (
    "Options marked Repeatable may be given more than once in a single command. "
    "Options marked \"Accepts @FILE\" read their value from a file (@- reads stdin), which is "
    "the way to pass text containing backticks, dollar signs or newlines; write a literal "
    "leading @ as @@. The tickets repo is found from this script, so any working directory works."
)


def _annotate_option_help(parser: argparse.ArgumentParser) -> None:
    """Append the Repeatable and @FILE notes to every option of a parser and its subparsers.

    :param parser: parser whose option help strings are rewritten in place.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                _annotate_option_help(subparser)
            continue
        help_text = (action.help or "").strip()
        if isinstance(action, argparse._AppendAction) and "Repeatable" not in help_text:
            help_text = f"{help_text} Repeatable.".strip()
        if action.type is text_option and "@FILE" not in help_text:
            help_text = f"{help_text} Accepts @FILE (@- for stdin).".strip()
        action.help = help_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tix",
        description="Ixdar ticket board generator, viewer and editor.",
        epilog=HELP_EPILOG,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("board", help="Regenerate BOARD.md from all ticket JSONs.")

    backlog_parser = sub.add_parser(
        "backlog",
        help="Print actionable tickets for a specific repo.",
    )
    backlog_parser.add_argument(
        "repo",
        help="Repository name to filter by (e.g. Blender-Procedural-Human).",
    )

    sub.add_parser("archive", help="Move all DONE tickets from content/ to done/.")

    priorities_parser = sub.add_parser(
        "priorities",
        help="List tickets grouped by epic+priority. Hides DONE by default.",
    )
    priorities_parser.add_argument(
        "--epic",
        default=None,
        help="Restrict to a single epic prefix (e.g. PATCH).",
    )
    priorities_parser.add_argument(
        "--status",
        default=None,
        help="Filter by status (TODO, IN_PROGRESS, DONE).",
    )
    priorities_parser.add_argument(
        "--all",
        action="store_true",
        dest="include_done",
        help="Include DONE tickets.",
    )

    next_id_parser = sub.add_parser(
        "next-id",
        help="Print the next available ticket ID for an epic.",
    )
    next_id_parser.add_argument(
        "epic",
        help="Epic prefix (e.g. BLEN, ENG, TRADE).",
    )

    mark_parser = sub.add_parser(
        "mark",
        help="Update ticket lifecycle state.",
    )
    mark_sub = mark_parser.add_subparsers(dest="mark_command")

    mark_done_parser = mark_sub.add_parser(
        "done",
        help="Mark a ticket DONE and regenerate BOARD.md. Refuses while a worktree named after "
             "the ticket is dirty or ahead of the main branch.",
    )
    mark_done_parser.add_argument(
        "ticket_id",
        help="Ticket ID to mark done (e.g. IX-3).",
    )
    mark_done_parser.add_argument(
        "--force",
        action="store_true",
        help="Mark DONE even when the ticket's worktree still holds unmerged work.",
    )

    mark_review_parser = mark_sub.add_parser(
        "review",
        help="Mark a ticket REVIEW (implemented, awaiting the user's verification and merge) and regenerate BOARD.md.",
    )
    mark_review_parser.add_argument(
        "ticket_id",
        help="Ticket ID to mark for review (e.g. IX-3).",
    )

    mark_ip_parser = mark_sub.add_parser(
        "in_progress",
        help="Mark a ticket IN_PROGRESS and regenerate BOARD.md.",
    )
    mark_ip_parser.add_argument(
        "ticket_id",
        help="Ticket ID to mark in progress (e.g. IX-3).",
    )

    mark_todo_parser = mark_sub.add_parser(
        "todo",
        help="Mark a ticket TODO (rollback) and regenerate BOARD.md.",
    )
    mark_todo_parser.add_argument(
        "ticket_id",
        help="Ticket ID to mark todo (e.g. IX-3).",
    )

    mark_pinned_parser = mark_sub.add_parser(
        "pinned",
        help="Mark a ticket PINNED (end-goal/destination) and regenerate BOARD.md.",
    )
    mark_pinned_parser.add_argument(
        "ticket_id",
        help="Ticket ID to mark pinned (e.g. PATCH-37).",
    )

    show_parser = sub.add_parser(
        "show",
        help="Print a full ticket JSON, whether it lives under content/ or done/.",
    )
    show_parser.add_argument(
        "ticket_id",
        help="Ticket ID to show (e.g. IX-3).",
    )
    show_parser.add_argument(
        "--indices",
        action="store_true",
        help="Also list todos and unknowns numbered for --resolve-todo/--resolve-unknown.",
    )

    create_parser = sub.add_parser(
        "create",
        help="Create a new ticket JSON and regenerate BOARD.md.",
        epilog=HELP_EPILOG,
    )
    create_parser.add_argument(
        "--from-json",
        dest="from_json",
        default=None,
        help="Read the whole ticket from a JSON file (- for stdin); other options override or "
             "extend its fields.",
    )
    create_parser.add_argument("--epic", default=None, help="Epic prefix (e.g. IX, DSL).")
    create_parser.add_argument("--repo", default=None, help="Repository name (e.g. Ixdar).")
    create_parser.add_argument(
        "--title",
        default=None,
        type=text_option,
        help="Ticket title.",
    )
    create_parser.add_argument(
        "--description",
        default=None,
        type=text_option,
        help="Full description.",
    )
    create_parser.add_argument(
        "--subsystem",
        action="append",
        default=[],
        help="Subsystem id from subsystems.json (e.g. platform, rendering).",
    )
    create_parser.add_argument(
        "--blocked-by",
        action="append",
        default=[],
        dest="blocked_by",
        help="Ticket ID this ticket waits on; the other ticket gets a reciprocal blocks entry.",
    )
    create_parser.add_argument(
        "--blocks",
        action="append",
        default=[],
        dest="blocks",
        help="Ticket ID that waits on this ticket; the other ticket gets a reciprocal blocked-by entry.",
    )
    create_parser.add_argument(
        "--unknown",
        action="append",
        default=[],
        dest="unknowns",
        type=text_option,
        help="Open question line.",
    )
    create_parser.add_argument(
        "--related-file",
        action="append",
        default=[],
        dest="related_files",
        help="Related file path.",
    )
    create_parser.add_argument(
        "--priority",
        type=int,
        default=None,
        help="Lower = higher priority (default 3).",
    )
    create_parser.add_argument(
        "--definition-of-done",
        dest="definition_of_done",
        default=None,
        type=text_option,
        help="Numbered definition of done text.",
    )
    create_parser.add_argument(
        "--testing-plan",
        dest="testing_plan",
        default=None,
        type=text_option,
        help="Numbered testing plan text.",
    )
    create_parser.add_argument(
        "--todo",
        action="append",
        default=[],
        dest="todos",
        type=text_option,
        help="Todo line.",
    )

    update_parser = sub.add_parser(
        "update",
        help="Patch fields on an existing ticket JSON and regenerate BOARD.md.",
        epilog=HELP_EPILOG,
    )
    update_parser.add_argument(
        "ticket_id",
        help="Ticket ID (e.g. FLOWER-1).",
    )
    update_parser.add_argument(
        "--append-description",
        dest="append_description",
        default=None,
        type=text_option,
        help="Text appended to description (after a blank line if non-empty).",
    )
    update_parser.add_argument(
        "--definition-of-done",
        dest="definition_of_done",
        default=None,
        type=text_option,
        help="Replace definition-of-done entirely.",
    )
    update_parser.add_argument(
        "--append-definition-of-done",
        dest="append_definition_of_done",
        default=None,
        type=text_option,
        help="Append to definition-of-done.",
    )
    update_parser.add_argument(
        "--testing-plan",
        dest="testing_plan",
        default=None,
        type=text_option,
        help="Replace testing-plan entirely.",
    )
    update_parser.add_argument(
        "--append-testing-plan",
        dest="append_testing_plan",
        default=None,
        type=text_option,
        help="Append to testing-plan.",
    )
    update_parser.add_argument(
        "--status",
        dest="status",
        default=None,
        help="Set status: TODO, IN_PROGRESS, REVIEW (implemented, awaiting the user's merge), or DONE.",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --status DONE even when the ticket's worktree still holds unmerged work.",
    )
    update_parser.add_argument(
        "--add-unknown",
        action="append",
        default=[],
        dest="add_unknowns",
        type=text_option,
        help="Append one unknowns[] line.",
    )
    update_parser.add_argument(
        "--add-changes",
        action="append",
        default=[],
        dest="add_changes",
        type=text_option,
        help="Append one changes-made[] line.",
    )
    update_parser.add_argument(
        "--add-todo",
        action="append",
        default=[],
        dest="add_todos",
        type=text_option,
        help="Append one todos[] line.",
    )
    update_parser.add_argument(
        "--add-related-file",
        action="append",
        default=[],
        dest="add_related_files",
        help="Append one related-files[] path.",
    )
    update_parser.add_argument(
        "--resolve-todo",
        action="append",
        default=[],
        dest="resolve_todos",
        type=int,
        metavar="N",
        help="Remove todos[] entry N (one-based, numbering as shown by show --indices) and print it.",
    )
    update_parser.add_argument(
        "--resolve-unknown",
        action="append",
        default=[],
        dest="resolve_unknowns",
        type=int,
        metavar="N",
        help="Remove unknowns[] entry N (one-based, numbering as shown by show --indices) and print it.",
    )
    update_parser.add_argument(
        "--add-blocked-by",
        action="append",
        default=[],
        dest="add_blocked_by",
        help="Append one blocked-by[] ticket ID; a reciprocal blocks entry is written.",
    )
    update_parser.add_argument(
        "--add-blocks",
        action="append",
        default=[],
        dest="add_blocks",
        help="Append one blocks[] ticket ID; a reciprocal blocked-by entry is written.",
    )

    _annotate_option_help(parser)
    return parser


def _create_fields_from_args(args: argparse.Namespace) -> dict:
    """Merge --from-json fields with the create options given on the command line.

    :param args: parsed create arguments.
    :return: keyword arguments for create_ticket, with defaults filled in.
    """
    fields: dict = create_fields_from_json(args.from_json) if args.from_json else {}
    for name in ("epic", "repo", "title", "description", "definition_of_done", "testing_plan"):
        value = getattr(args, name)
        if value is not None:
            fields[name] = value
    for name in ("subsystem", "todos", "unknowns", "related_files", "blocked_by", "blocks"):
        values = list(getattr(args, name) or [])
        if values:
            fields[name] = list(fields.get(name, [])) + values
    if args.priority is not None:
        fields["priority"] = args.priority

    missing = [flag for name, flag in REQUIRED_CREATE_FIELDS if not fields.get(name)]
    if missing:
        raise ValueError("create is missing required field(s): " + ", ".join(missing))

    fields.setdefault("priority", 3)
    for name in ("todos", "unknowns", "related_files", "blocked_by", "blocks"):
        fields.setdefault(name, [])
    return fields


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Run the subcommand named by the parsed arguments.

    :param parser: parser used, for printing help when no subcommand was given.
    :param args: parsed arguments.
    :return: process exit code.
    """
    if args.command == "backlog":
        print_repo_tickets(args.repo)
    elif args.command == "board":
        regenerate_board()
    elif args.command == "archive":
        archive_done()
    elif args.command == "priorities":
        print_priorities(
            epic=args.epic,
            status_filter=args.status,
            include_done=args.include_done,
        )
    elif args.command == "next-id":
        print_next_id(args.epic)
    elif args.command == "mark" and args.mark_command == "done":
        mark_ticket_done(args.ticket_id, force=args.force)
    elif args.command == "mark" and args.mark_command == "in_progress":
        mark_ticket_status(args.ticket_id, "IN_PROGRESS")
    elif args.command == "mark" and args.mark_command == "review":
        mark_ticket_status(args.ticket_id, "REVIEW")
    elif args.command == "mark" and args.mark_command == "todo":
        mark_ticket_status(args.ticket_id, "TODO")
    elif args.command == "mark" and args.mark_command == "pinned":
        mark_ticket_status(args.ticket_id, "PINNED")
    elif args.command == "show":
        show_ticket(args.ticket_id, indices=args.indices)
    elif args.command == "create":
        path = create_ticket(**_create_fields_from_args(args))
        print(f"Created {path}")
    elif args.command == "update":
        update_ticket(
            args.ticket_id,
            add_blocked_by=list(args.add_blocked_by or []),
            add_blocks=list(args.add_blocks or []),
            append_description=args.append_description,
            definition_of_done=args.definition_of_done,
            testing_plan=args.testing_plan,
            append_definition_of_done=args.append_definition_of_done,
            append_testing_plan=args.append_testing_plan,
            add_unknowns=list(args.add_unknowns or []),
            add_changes=list(args.add_changes or []),
            add_todos=list(args.add_todos or []),
            add_related_files=list(args.add_related_files or []),
            resolve_todos=list(args.resolve_todos or []),
            resolve_unknowns=list(args.resolve_unknowns or []),
            status=args.status,
            force=args.force,
        )
    else:
        parser.print_help()
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for both `tix` and `python generate_board.py`.

    :param argv: argument list to parse, defaulting to sys.argv[1:].
    :return: process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(parser, args)
    except (UnmergedWorktreeError, IndexError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
