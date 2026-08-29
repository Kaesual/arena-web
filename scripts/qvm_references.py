# SPDX-License-Identifier: GPL-2.0-or-later
"""Extract the content references of the pinned ioquake3 `baseq3` QVM sources.

The three `baseq3` QVMs are the gamecode the prototype targets, so the set of
files a content pack must provide is a property of *their* sources, not of any
OpenArena packaging. This module reads the exact translation units
`cmake/basegame.cmake` compiles into `cgame`, `qagame` and `ui`, follows their
local headers, drops the `MISSIONPACK` branches that the `baseq3` QVMs are not
built with, and reports the asset paths that survive.

References are split into two kinds because only one of them is decidable
statically: plain literals such as `sound/world/jumppad.wav`, and format
templates such as `models/players/%s/lower.md3` whose expansion depends on
runtime state. The caller supplies the profile that expands the templates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MODULE_MACROS = {"cgame": "CGAME", "qagame": "QAGAME", "ui": "UI"}

# code/tools/lcc/etc/bytecode.c passes -DQ3_VM to every QVM translation unit.
ALWAYS_DEFINED = frozenset({"Q3_VM", "__STDC__", "__STRICT_ANSI__"})

# The baseq3 QVMs are built by cmake/basegame.cmake without MISSIONPACK, so its
# branches cannot reference anything a baseq3 content pack has to provide.
ALWAYS_UNDEFINED = frozenset({"MISSIONPACK"})

# Directories a reference may name. `ui/` is deliberately absent: it belongs to
# the missionpack menu system, which these QVMs do not contain.
ASSET_ROOTS = (
    "botfiles",
    "demos",
    "fonts",
    "gfx",
    "icons",
    "levelshots",
    "maps",
    "menu",
    "models",
    "music",
    "scripts",
    "sound",
    "sprites",
    "textures",
    "video",
)

_ASSET_RE = re.compile(
    r"^(?:%s)/[A-Za-z0-9_%%./+-]*$" % "|".join(ASSET_ROOTS),
)
_FORMAT_RE = re.compile(r"%[-0-9.]*[a-zA-Z]")
_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_CMAKE_LIST_RE = re.compile(r"set\((\w+)\s(.*?)\)\s*$", re.DOTALL | re.MULTILINE)
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)


class ReferenceError(ValueError):
    """Raised when the pinned engine tree does not match this extractor."""


@dataclass(frozen=True)
class ModuleReferences:
    module: str
    literals: tuple[str, ...]
    templates: tuple[str, ...]


def _cmake_lists(text: str) -> dict[str, list[str]]:
    lists: dict[str, list[str]] = {}
    for name, body in _CMAKE_LIST_RE.findall(text):
        lists[name] = body.split()
    return lists


def _expand(lists: dict[str, list[str]], name: str, source_dir: str) -> list[str]:
    if name not in lists:
        raise ReferenceError(f"cmake/basegame.cmake does not define {name}")
    resolved: list[str] = []
    for token in lists[name]:
        token = token.replace("${SOURCE_DIR}", source_dir)
        reference = re.fullmatch(r"\$\{(\w+)\}", token)
        if reference:
            resolved.extend(_expand(lists, reference.group(1), source_dir))
        else:
            resolved.append(token)
    return resolved


def baseq3_translation_units(ioq3_root: Path) -> dict[str, list[Path]]:
    """Return the C sources of each `baseq3` QVM exactly as CMake lists them."""
    basegame = ioq3_root / "cmake" / "basegame.cmake"
    try:
        text = basegame.read_text(encoding="utf-8")
    except OSError as error:
        raise ReferenceError(f"cannot read {basegame}: {error}") from error
    lists = _cmake_lists(text)
    modules = {
        "cgame": "CGAME_SOURCES_BASEGAME",
        "qagame": "GAME_SOURCES_BASEGAME",
        "ui": "UI_SOURCES_BASEGAME",
    }
    units: dict[str, list[Path]] = {}
    for module, list_name in modules.items():
        paths = []
        for entry in _expand(lists, list_name, "code"):
            if not entry.endswith(".c"):
                continue
            path = ioq3_root / entry
            if not path.is_file():
                raise ReferenceError(f"{list_name} names a missing source {entry}")
            paths.append(path)
        if not paths:
            raise ReferenceError(f"{list_name} resolved to no C sources")
        units[module] = sorted(set(paths))
    return units


def _reachable_headers(sources: list[Path], ioq3_root: Path) -> list[Path]:
    seen: set[Path] = set()
    queue = list(sources)
    headers: list[Path] = []
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for include in _INCLUDE_RE.findall(text):
            candidate = (current.parent / include).resolve()
            try:
                candidate.relative_to(ioq3_root.resolve())
            except ValueError:
                continue
            if candidate.is_file() and candidate not in seen:
                headers.append(candidate)
                queue.append(candidate)
    return sorted(set(headers) - set(sources))


def _condition_value(
    directive: str, expression: str, defined: frozenset[str]
) -> bool | None:
    """Return the value of a preprocessor condition, or None when undecidable."""
    expression = expression.strip()
    if directive == "ifdef":
        return _known(expression, defined)
    if directive == "ifndef":
        value = _known(expression, defined)
        return None if value is None else not value
    negated = False
    if expression.startswith("!"):
        negated = True
        expression = expression[1:].strip()
    match = re.fullmatch(r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)", expression)
    if not match:
        return None
    name = match.group(1) or match.group(2)
    value = _known(name, defined)
    if value is None:
        return None
    return not value if negated else value


def _known(name: str, defined: frozenset[str]) -> bool | None:
    name = name.strip()
    if not re.fullmatch(r"\w+", name):
        return None
    if name in defined:
        return True
    if name in ALWAYS_UNDEFINED:
        return False
    return None


def select_compiled_lines(text: str, defined: frozenset[str]) -> list[str]:
    """Drop the conditional blocks the QVM build cannot compile.

    A condition this evaluator cannot decide keeps *both* branches, so the
    result is a superset of the compiled text and the closure it feeds never
    silently loses a reference. Only conditions built from macros with a known
    state — the module macro and `MISSIONPACK` — remove anything.
    """
    kept: list[str] = []
    # Each stack entry is (emitting_now, decided_by_us, any_branch_taken).
    stack: list[tuple[bool, bool, bool]] = []
    emitting = True
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)", stripped)
        if not match:
            if emitting:
                kept.append(line)
            continue
        directive, expression = match.group(1), match.group(2)
        if directive in ("ifdef", "ifndef", "if"):
            value = _condition_value(directive, expression, defined)
            parent = emitting
            if value is None:
                stack.append((parent, False, True))
                emitting = parent
            else:
                stack.append((parent, True, bool(value)))
                emitting = parent and bool(value)
        elif directive == "elif":
            if not stack:
                continue
            parent, decided, taken = stack[-1]
            value = _condition_value("if", expression, defined)
            if decided and value is not None:
                emitting = parent and not taken and bool(value)
                stack[-1] = (parent, True, taken or bool(value))
            else:
                emitting = parent
                stack[-1] = (parent, False, True)
        elif directive == "else":
            if not stack:
                continue
            parent, decided, taken = stack[-1]
            emitting = parent and (not taken if decided else True)
            stack[-1] = (parent, decided, True)
        elif directive == "endif":
            if stack:
                parent, _decided, _taken = stack.pop()
                emitting = parent
    return kept


def _string_literals(lines: list[str]) -> set[str]:
    literals: set[str] = set()
    for line in lines:
        for raw in _STRING_RE.findall(line):
            literals.add(raw.replace('\\"', '"'))
    return literals


def module_references(
    sources: list[Path], ioq3_root: Path, module: str
) -> ModuleReferences:
    """Return the asset literals and format templates one QVM can reference."""
    defined = frozenset(ALWAYS_DEFINED | {MODULE_MACROS[module]})
    literals: set[str] = set()
    templates: set[str] = set()
    for path in sources + _reachable_headers(sources, ioq3_root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw in _string_literals(select_compiled_lines(text, defined)):
            for token in raw.split():
                token = token.strip().replace("\\n", "")
                if not _ASSET_RE.fullmatch(token):
                    continue
                if _FORMAT_RE.search(token):
                    templates.add(token)
                else:
                    literals.add(token)
    return ModuleReferences(module, tuple(sorted(literals)), tuple(sorted(templates)))


def baseq3_references(ioq3_root: Path) -> dict[str, ModuleReferences]:
    """Return the reference sets of all three pinned `baseq3` QVMs."""
    units = baseq3_translation_units(ioq3_root)
    return {
        module: module_references(sources, ioq3_root, module)
        for module, sources in sorted(units.items())
    }
