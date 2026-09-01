# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic assembly of the audited arena-web content pack.

The pack is not a curated file list: it is the transitive closure of what the
pinned ioquake3 `baseq3` QVM sources reference, plus the maps, player
presentations and bot data named by the committed recipe. Every member is read
out of a digest-verified upstream archive in the same run that writes it, so
the bytes in the pack are derived from bytes this code checked itself.

Nothing here consults the clock, the environment, the working directory or an
unpinned network location. Two runs from the same recipe and the same verified
archives produce the same PK3 byte for byte.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import tarfile
import zipfile
from fnmatch import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from game_assets import (
    GENERATED_IMAGE_NAMES,
    IMAGE_EXTENSIONS,
    SOUND_EXTENSIONS,
    AssetFormatError,
    candidate_paths,
    is_engine_path,
    parse_bsp,
    parse_md3,
    parse_shader_file,
    parse_skin,
    shader_file_precedence,
)
from metadata import (
    CONTENT_SCHEMA,
    MetadataError,
    _canonical_json_identity,
    _fail,
    _load_json,
    validate_content_provenance,
)

CHUNK_SIZE = 1024 * 1024

# The pack must contain content only. Gamecode is the pinned ioquake3 baseq3
# QVMs, which the prototype plan selected and which no content source may
# replace, and the engine-specific GLSL of a different engine has no meaning
# here.
FORBIDDEN_MEMBER_PATTERNS = (
    (re.compile(r"(?i)\.qvm$"), "compiled gamecode"),
    (re.compile(r"(?i)^vm/"), "gamecode directory"),
    (re.compile(r"(?i)^glsl/"), "OpenArena engine shader programs"),
    (re.compile(r"(?i)\.(dll|so|dylib|exe)$"), "native code"),
)

# Deterministic ZIP metadata. 1980-01-01 00:00:00 is the earliest timestamp the
# ZIP format can express, so no ambient clock value can reach the archive.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_UNIX_MODE = 0o100644
ZIP_CREATE_SYSTEM = 3  # Unix, so the mode above is the one a reader sees.
ZIP_COMPRESS_LEVEL = 9


class ContentError(ValueError):
    """Raised when the recipe, a source archive or the closure is not usable."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _game_path(value: str) -> str:
    """Normalise an engine path reference to its lower-case lookup form."""
    return value.replace("\\", "/").strip().lstrip("/").lower()


def _archive_member_name(name: str, source: RecipeSource) -> str:
    """Return an archive member's normalised name, refusing to leave its root.

    `lstrip` would be a character-class strip here, so the name is normalised
    properly and then required to stay under the recipe's declared archive
    root: a member that escapes it is a malformed or hostile archive, not
    something to silently skip.
    """
    normalised = posixpath.normpath(name)
    root = source.archive_root
    if normalised != root and not normalised.startswith(f"{root}/"):
        raise ContentError(
            f"{source.id}: archive member {name!r} escapes the archive root {root!r}"
        )
    return normalised


@dataclass(frozen=True)
class RecipeSource:
    """What reading one upstream archive needs.

    The licence fields of a recipe source deliberately do not appear here:
    `ClosureBuilder` and `build_provenance` read them from the recipe record
    itself, and a second copy would be a second source of truth for a rule that
    decides what may be packaged.
    """

    id: str
    file_name: str
    sha256: str
    size: int
    archive_root: str
    trees: tuple[str, ...]
    documents: tuple[str, ...]
    precedence: int


@dataclass
class SourceMember:
    source_id: str
    source_path: str
    game_path: str
    precedence: int
    data: bytes


class SourceSet:
    """The verified upstream archives, indexed by the game path they provide."""

    def __init__(self, sources: list[RecipeSource], archive_dir: Path) -> None:
        self._members: dict[str, SourceMember] = {}
        for source in sorted(sources, key=lambda item: item.precedence):
            archive = archive_dir / source.file_name
            if not archive.is_file():
                raise ContentError(
                    f"{source.id}: {archive} is missing; "
                    "run scripts/fetch-content-sources.sh"
                )
            # Verify and read the *same* open file, so nothing can replace the
            # archive between the digest check and the bytes that are packaged.
            with archive.open("rb") as handle:
                self._verify(source, archive.name, handle)
                handle.seek(0)
                self._load(source, handle)

    @staticmethod
    def _verify(source: RecipeSource, name: str, handle) -> None:
        digest = hashlib.sha256()
        size = 0
        while chunk := handle.read(CHUNK_SIZE):
            size += len(chunk)
            digest.update(chunk)
        if size != source.size:
            raise ContentError(
                f"{source.id}: {name} is {size} bytes, recipe pins {source.size}"
            )
        actual = digest.hexdigest()
        if actual != source.sha256:
            raise ContentError(
                f"{source.id}: {name} is sha256:{actual}, "
                f"recipe pins sha256:{source.sha256}"
            )

    def _load(self, source: RecipeSource, handle) -> None:
        wanted_trees = tuple(f"{source.archive_root}/{tree}/" for tree in source.trees)
        wanted_documents = {
            f"{source.archive_root}/{document}": document
            for document in source.documents
        }
        with tarfile.open(fileobj=handle, mode="r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                name = _archive_member_name(member.name, source)
                game_path: str | None = None
                if name in wanted_documents:
                    game_path = wanted_documents[name]
                else:
                    for tree in wanted_trees:
                        if name.startswith(tree):
                            game_path = name[len(tree) :]
                            break
                if game_path is None:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                key = _game_path(game_path)
                self._members[key] = SourceMember(
                    source_id=source.id,
                    source_path=member.name,
                    game_path=game_path,
                    precedence=source.precedence,
                    data=extracted.read(),
                )

    def __contains__(self, game_path: str) -> bool:
        return _game_path(game_path) in self._members

    def get(self, game_path: str) -> SourceMember | None:
        return self._members.get(_game_path(game_path))

    def paths(self) -> list[str]:
        return sorted(self._members)

    def list_directory(self, prefix: str) -> list[str]:
        prefix = _game_path(prefix)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return sorted(path for path in self._members if path.startswith(prefix))


@dataclass
class ClosureReport:
    members: dict[str, SourceMember] = field(default_factory=dict)
    unresolved: dict[str, str] = field(default_factory=dict)
    malformed: dict[str, str] = field(default_factory=dict)
    accepted_unresolved: list[str] = field(default_factory=list)
    stale_acceptances: list[str] = field(default_factory=list)
    shader_files: set[str] = field(default_factory=set)
    # Which shader file the closure resolved each shader *name* to. The set of
    # files above cannot answer that, and cross-archive shader precedence
    # (§4.2) is a property of names, not of files.
    shader_names: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.unresolved and not self.stale_acceptances


class ShaderIndex:
    """Shader definitions across all candidate `scripts/*.shader` files.

    The engine concatenates the shader files in the reverse of the listing
    order and takes the first definition it meets; for this pack that listing
    is alphabetical because `write_pk3` stores members sorted, so a definition
    in a later-sorting file wins. `shader_file_precedence` states the coupling.
    """

    def __init__(self, sources: SourceSet) -> None:
        self._by_name: dict[str, tuple[str, tuple[str, ...]]] = {}
        files = [
            path
            for path in sources.paths()
            if path.startswith("scripts/") and path.endswith(".shader")
        ]
        for path in shader_file_precedence(files):
            member = sources.get(path)
            if member is None:  # pragma: no cover - paths() came from sources
                continue
            try:
                definitions = parse_shader_file(member.data.decode("latin-1"))
            except AssetFormatError as error:
                raise ContentError(f"{path}: {error}") from error
            for definition in definitions:
                name = _game_path(definition.name)
                if name not in self._by_name:
                    self._by_name[name] = (path, definition.images)

    def lookup(self, name: str) -> tuple[str, tuple[str, ...]] | None:
        return self._by_name.get(_game_path(name))


class ClosureBuilder:
    """Expand every required reference of the profile into concrete members.

    One builder produces one archive. The three per-run memos below —
    ``_seen``, ``report.members`` and ``_accepted_hit`` — are why an archive
    needs its own builder rather than a shared one: under a shared builder a
    member two maps reference would belong to whichever map was walked first,
    so adding a map that sorts earlier would migrate that member out of an
    archive that already exists. The ``SourceSet`` and the ``ShaderIndex``
    stay shared, which is what makes the same game path resolve to the same
    bytes in every archive.

    ``shaders``, ``accepted_unresolved`` and ``generated_members`` may be
    supplied per archive; omitted, they come from the recipe, which is the
    single-archive behaviour.
    """

    def __init__(
        self,
        sources: SourceSet,
        recipe: dict[str, Any],
        *,
        shaders: ShaderIndex | None = None,
        accepted_unresolved: Iterable[dict[str, Any]] | None = None,
        generated_members: Iterable[str] | None = None,
    ) -> None:
        self.sources = sources
        self.recipe = recipe
        self.shaders = ShaderIndex(sources) if shaders is None else shaders
        self.report = ClosureReport()
        self._seen: set[tuple[str, str]] = set()
        if accepted_unresolved is None:
            accepted_unresolved = recipe.get("acceptedUnresolved", [])
        self._accepted = {
            _game_path(entry["reference"]): entry["reason"]
            for entry in accepted_unresolved
        }
        self._accepted_hit: set[str] = set()
        if generated_members is None:
            generated_members = recipe.get("generatedMembers", ())
        self._generated = {_game_path(path) for path in generated_members}
        # The exclusion is global, not per-source: a path that one source
        # declares as differently licensed must not slip in because a
        # higher-precedence source happens to provide the same path.
        self._non_default_license = tuple(
            sorted(
                {
                    (record["id"], pattern.lower())
                    for record in recipe.get("sources", ())
                    for pattern in record.get("nonDefaultLicensePaths", ())
                }
            )
        )

    # -- resolution ------------------------------------------------------

    def _take(self, path: str, origin: str) -> bool:
        key = _game_path(path)
        if key in self._generated:
            # The recipe replaces this path with a product-owned file, so the
            # reference is satisfied without packaging the upstream version.
            return True
        member = self.sources.get(path)
        if member is None:
            return False
        for pattern, reason in FORBIDDEN_MEMBER_PATTERNS:
            if pattern.search(key):
                raise ContentError(f"{origin}: refuses to package {path} ({reason})")
        for declaring_source, pattern in self._non_default_license:
            if fnmatch(key, pattern):
                raise ContentError(
                    f"{origin}: {path} is covered by pattern {pattern!r}, which "
                    f"{declaring_source} declares as differently licensed from its "
                    "source expression; it must be selected out or declared separately"
                )
        self.report.members.setdefault(key, member)
        return True

    def _unresolved(self, reference: str, origin: str) -> None:
        key = _game_path(reference)
        if key in self._accepted:
            self._accepted_hit.add(key)
            return
        if not is_engine_path(reference):
            # Still a reference the engine would look up and fail; recording it
            # under its own heading keeps an editor artefact from reading like a
            # missing asset while leaving it visible.
            self.report.malformed.setdefault(reference, origin)
            return
        self.report.unresolved.setdefault(reference, origin)

    def add(self, reference: str, kind: str, origin: str) -> None:
        reference = reference.strip()
        if not reference or reference.endswith("/"):
            return
        if _game_path(reference) in GENERATED_IMAGE_NAMES:
            return
        key = (kind, _game_path(reference))
        if key in self._seen:
            return
        self._seen.add(key)
        handler = getattr(self, f"_add_{kind}")
        handler(reference, origin)

    def _add_file(self, reference: str, origin: str) -> None:
        if not self._take(reference, origin):
            self._unresolved(reference, origin)

    def _add_image(self, reference: str, origin: str) -> None:
        for candidate in candidate_paths(reference, IMAGE_EXTENSIONS):
            if self._take(candidate, origin):
                return
        self._unresolved(reference, origin)

    def _add_sound(self, reference: str, origin: str) -> None:
        for candidate in candidate_paths(reference, SOUND_EXTENSIONS):
            if self._take(candidate, origin):
                return
        self._unresolved(reference, origin)

    def _add_shader(self, reference: str, origin: str) -> None:
        # tr_shader.c: R_FindShader strips the extension before it looks the
        # name up in the shader text, then falls back to R_FindImageFile with
        # the *original* name.
        stripped = re.sub(r"\.[^./]*$", "", reference)
        found = self.shaders.lookup(stripped)
        name = stripped
        if found is None:
            found = self.shaders.lookup(reference)
            name = reference
        if found is not None:
            shader_file, images = found
            self._take(shader_file, f"shader {reference}")
            self.report.shader_files.add(shader_file)
            self.report.shader_names.setdefault(_game_path(name), shader_file)
            for image in images:
                self.add(image, "image", f"shader {reference}")
            return
        # A shader name with no definition is an implicit single-stage shader
        # built from the image of the same name.
        self._add_image(reference, origin)

    def _add_model(self, reference: str, origin: str) -> None:
        member = self.sources.get(reference)
        if member is None:
            self._unresolved(reference, origin)
            return
        self._take(reference, origin)
        try:
            shaders = parse_md3(member.data)
        except AssetFormatError as error:
            raise ContentError(f"{reference}: {error}") from error
        for shader in shaders:
            self.add(shader, "shader", f"model {reference}")

    def _add_skin(self, reference: str, origin: str) -> None:
        member = self.sources.get(reference)
        if member is None:
            self._unresolved(reference, origin)
            return
        self._take(reference, origin)
        for shader in parse_skin(member.data.decode("latin-1")):
            self.add(shader, "shader", f"skin {reference}")

    def _add_bsp(self, reference: str, origin: str) -> None:
        member = self.sources.get(reference)
        if member is None:
            self._unresolved(reference, origin)
            return
        self._take(reference, origin)
        try:
            info = parse_bsp(member.data)
        except AssetFormatError as error:
            raise ContentError(f"{reference}: {error}") from error
        for shader in info.shaders:
            self.add(shader, "shader", f"map {reference}")
        for entity in info.entities:
            model = entity.get("model", "")
            if model.startswith("models/") and model.endswith(".md3"):
                self.add(model, "model", f"map {reference} entity")
            for key in ("noise", "music"):
                value = entity.get(key, "")
                if value and not value.startswith("*"):
                    self.add(value, "sound", f"map {reference} entity")

    def _add_botfile(self, reference: str, origin: str) -> None:
        member = self.sources.get(reference)
        if member is None:
            self._unresolved(reference, origin)
            return
        self._take(reference, origin)
        text = member.data.decode("latin-1")
        # botlib/be_ai_char.c and be_ai_weap.c call PC_SetBaseFolder("botfiles"),
        # so every include and every characteristic file name is resolved from
        # that folder rather than from the including file's directory.
        for include in re.findall(r'^\s*#\s*include\s+"([^"]+)"', text, re.MULTILINE):
            self.add(f"botfiles/{include}", "botfile", f"botfile {reference}")
        for value in re.findall(r'CHARACTERISTIC_\w+\s+"([\w./-]+\.c)"', text):
            self.add(f"botfiles/{value}", "botfile", f"botfile {reference}")

    # -- roots -----------------------------------------------------------

    def add_engine_reference(self, reference: str, origin: str) -> None:
        """Add one `baseq3` QVM reference under the kind its directory implies."""
        lowered = _game_path(reference)
        if lowered.startswith(("sound/", "music/")):
            kind = "sound"
        elif lowered.endswith(".md3"):
            kind = "model"
        elif lowered.endswith(".skin"):
            kind = "skin"
        elif lowered.startswith("botfiles/"):
            kind = "botfile"
        elif lowered.startswith("scripts/") or lowered.endswith((".cfg", ".txt")):
            kind = "file"
        else:
            # Everything the QVMs draw goes through trap_R_RegisterShader, which
            # falls back to an image of the same name when no shader defines it.
            kind = "shader"
        self.add(reference, kind, origin)

    def finish(self) -> ClosureReport:
        self.report.accepted_unresolved = sorted(self._accepted_hit)
        self.report.stale_acceptances = sorted(set(self._accepted) - self._accepted_hit)
        return self.report


def _require(record: dict[str, Any], key: str, path: str) -> Any:
    if key not in record:
        _fail(path, f"is missing required field {key!r}")
    return record[key]


def load_recipe(path: Path) -> dict[str, Any]:
    """Load and shallow-validate the committed content recipe."""
    recipe = _load_json(path)
    if not isinstance(recipe, dict):
        _fail(str(path), "must be an object")
    if recipe.get("formatVersion") != 2:
        _fail(str(path), "has an unsupported formatVersion")
    # derivedReferences is required, not optional: without it the builder would
    # silently drop closure root 1b and emit a smaller pack with a clean exit,
    # which is exactly the fail-open shape the category exists to close.
    for key in (
        "package",
        "basePackPath",
        "mapPackTemplate",
        "profile",
        "sources",
        "notices",
        "derivedReferences",
    ):
        _require(recipe, key, str(path))
    if not recipe["sources"]:
        _fail(f"{path}.sources", "must not be empty")
    return recipe


# One fragment carries everything about one map that the archive for that map
# must depend on, and nothing else: its arena definition, its own accepted
# unresolved references and its own generated members. Keeping them here rather
# than in three whole-set lists is what makes an archive's bytes independent of
# which other maps are in the build.
MAP_FRAGMENT_KEYS = ("acceptedUnresolved", "arena", "generatedMembers", "map")
MAP_FRAGMENT_DIRECTORY = "content/maps"


def map_fragment_path(map_name: str) -> str:
    return f"{MAP_FRAGMENT_DIRECTORY}/{map_name}.json"


def map_pack_path(recipe: dict[str, Any], map_name: str) -> str:
    """The manifest path of one map's archive, derived rather than listed.

    A per-map archive path in a flat recipe list would be one more whole-set
    field for a map to be added to and forgotten in. The template is checked
    once, in `load_map_fragments`.
    """
    return recipe["mapPackTemplate"].format(map=map_name)


MAP_FRAGMENT_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")

# One content-manifest input id per map fragment. The fragments are the content
# that decides what a map archive holds, so they must sit inside the release
# identity; this is the id under which each one does.
MAP_FRAGMENT_INPUT_PREFIX = "arena-web-map-"


def map_fragment_input_id(map_name: str) -> str:
    return f"{MAP_FRAGMENT_INPUT_PREFIX}{map_name}"


def arena_file_path(map_name: str) -> str:
    """The per-map arena file `G_LoadArenas` picks up from `scripts/*.arena`."""
    return f"scripts/{map_name}.arena"


def generated_map_members(recipe: dict[str, Any], map_name: str) -> tuple[str, ...]:
    """Exactly what a map archive generates rather than packages."""
    return (recipe["noticeFile"], arena_file_path(map_name))


def load_map_fragments(root: Path, recipe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Load and validate every per-map recipe fragment.

    **The fragment directory is the map set.** The root recipe deliberately
    holds no list of maps: it is the base archive's own selection input, and its
    digest is what the base's notice carries, so a map set inside it would move
    the base's bytes — and every existing map archive's — whenever a map was
    added. That is precisely what §8.1's acceptance test forbids.

    The fragments do not thereby escape the release identity. The content
    manifest records one input per fragment with its digest
    (`map_fragment_input_id`), the manifest is an authority whose own digest is
    a `compatibility` member, and `release_index.validate_release_index` checks
    that set against this directory in both directions. Content cannot join the
    build without joining the identity; it merely joins it one authority further
    down than the root recipe.
    """
    directory = root / MAP_FRAGMENT_DIRECTORY
    fragments: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return fragments
    for item in sorted(directory.iterdir()):
        if item.is_symlink():
            # A symlink's target may sit outside the repository, so the digest
            # the content manifest records would be of content the release does
            # not contain — content joining the build without joining the
            # identity, which is the one thing this directory may not allow.
            raise ContentError(
                f"{MAP_FRAGMENT_DIRECTORY}/{item.name} is a symlink; a fragment "
                "must be a file in this repository"
            )
        if item.is_dir():
            raise ContentError(
                f"{MAP_FRAGMENT_DIRECTORY} holds a directory, {item.name}; it may "
                "contain only one JSON fragment per map"
            )
        if not item.name.endswith(".json"):
            raise ContentError(
                f"{MAP_FRAGMENT_DIRECTORY}/{item.name} is not a .json fragment"
            )
        map_name = item.name[: -len(".json")]
        if not MAP_FRAGMENT_NAME.fullmatch(map_name):
            raise ContentError(
                f"{MAP_FRAGMENT_DIRECTORY}/{item.name} is not named after a map"
            )
        relative = map_fragment_path(map_name)
        fragment = _load_json(item)
        if not isinstance(fragment, dict) or set(fragment) != set(MAP_FRAGMENT_KEYS):
            raise ContentError(
                f"{relative} must have exactly the fields {list(MAP_FRAGMENT_KEYS)}"
            )
        if fragment["map"] != map_name:
            raise ContentError(
                f"{relative} declares map {fragment['map']!r}, but its file name "
                f"says {map_name!r}"
            )
        arena = fragment["arena"]
        if not isinstance(arena, dict) or arena.get("map") != map_name:
            raise ContentError(f"{relative} arena must define map {map_name!r}")
        for key in ("acceptedUnresolved", "generatedMembers"):
            if not isinstance(fragment[key], list):
                raise ContentError(f"{relative} {key} must be an array")
        # `generatedMembers` satisfies a reference *without packaging anything*,
        # so an unbounded list is a silent suppression list: naming this map's
        # own AAS there would drop bot navigation with no unresolved reference
        # and no failing gate. A map archive generates exactly two members.
        expected_generated = sorted(generated_map_members(recipe, map_name))
        if sorted(fragment["generatedMembers"]) != expected_generated:
            raise ContentError(
                f"{relative} generatedMembers is {sorted(fragment['generatedMembers'])}, "
                f"and a map archive generates exactly {expected_generated}"
            )
        fragments[map_name] = fragment
    return fragments


def check_map_pack_template(recipe: dict[str, Any]) -> None:
    template = recipe.get("mapPackTemplate")
    if not isinstance(template, str) or template.count("{map}") != 1:
        raise ContentError(
            f"recipe mapPackTemplate {template!r} must contain '{{map}}' exactly once"
        )


def recipe_sources(recipe: dict[str, Any]) -> list[RecipeSource]:
    sources = []
    for record in recipe["sources"]:
        sources.append(
            RecipeSource(
                id=record["id"],
                file_name=record["fileName"],
                sha256=record["sha256"],
                size=record["size"],
                archive_root=record["archiveRoot"],
                trees=tuple(record.get("trees", ())),
                documents=tuple(record.get("documents", ())),
                precedence=record["precedence"],
            )
        )
    precedences = [source.precedence for source in sources]
    if len(set(precedences)) != len(precedences):
        raise ContentError("recipe sources must have distinct precedence values")
    return sources


def write_pk3(members: dict[str, bytes], output: Path) -> None:
    """Write a PK3 with a fixed member order and no ambient metadata."""
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(members):
            info = zipfile.ZipInfo(filename=path, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = ZIP_CREATE_SYSTEM
            info.external_attr = ZIP_UNIX_MODE << 16
            info.internal_attr = 0
            info.create_version = 20
            info.extract_version = 20
            # The compression level must be passed here: a level given to the
            # ZipFile constructor is ignored for a caller-supplied ZipInfo, so
            # the archive would silently fall back to zlib's default.
            archive.writestr(info, members[path], compresslevel=ZIP_COMPRESS_LEVEL)
    output.write_bytes(buffer.getvalue())


@dataclass
class AssembledArchive:
    """One finished archive, with everything its own records need.

    `recipe_input` and `recipe_identity` are this archive's *own* committed
    selection input — `content/base.json` for the base and
    `content/maps/<name>.json` for a map. They are per archive, not per build,
    because the generated notice carries them: a whole-set digest inside an
    archive's bytes would move every archive whenever any part of the set moved.
    """

    path: str
    members: dict[str, bytes]
    origins: dict[str, tuple[str, str, str]]
    recipe_input: str
    recipe_identity: str


def provenance_sources(
    recipe: dict[str, Any],
    used_source_ids: set[str],
    *,
    generated_identity: str | None = None,
) -> list[dict[str, Any]]:
    """Return the sorted provenance source records one archive actually used.

    `generated_identity` replaces the product's own source identity with the
    digest of the selection input that produced *this* archive.
    """
    records = []
    generated_id = recipe["generatedSource"]["id"]
    for record in list(recipe["sources"]) + [recipe["generatedSource"]]:
        if record["id"] not in used_source_ids:
            continue
        identity = record["sourceIdentity"]
        revision = record["preferredSourceRevision"]
        if record["id"] == generated_id and generated_identity is not None:
            identity = generated_identity
            revision = generated_identity
        records.append(
            {
                "id": record["id"],
                "licenseEvidenceUrl": record["licenseEvidenceUrl"],
                "licenseExpression": record["licenseExpression"],
                "preferredSourceRevision": revision,
                "preferredSourceUrl": record["preferredSourceUrl"],
                "sourceIdentity": identity,
                "sourceUrl": record["sourceUrl"],
            }
        )
    return sorted(records, key=lambda item: item["id"])


def archive_provenance(
    recipe: dict[str, Any], archive: AssembledArchive
) -> dict[str, Any]:
    """The provenance record of one archive: its own sources and members."""
    notice_paths = tuple(sorted(recipe["notices"]))
    missing_notices = [path for path in notice_paths if path not in archive.members]
    if missing_notices:
        raise ContentError(
            f"{archive.path}: declared notice members are not packaged: "
            f"{missing_notices}"
        )
    source_records = provenance_sources(
        recipe,
        {archive.origins[path][0] for path in archive.members},
        generated_identity=archive.recipe_identity,
    )
    licenses = {record["id"]: record["licenseExpression"] for record in source_records}

    member_records = []
    for path in sorted(archive.members):
        source_id, source_path, transformation = archive.origins[path]
        expression = licenses[source_id]
        obligations = {"license-notice"}
        if any(
            token.startswith(("AGPL-", "GPL-", "LGPL-"))
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", expression)
        ):
            obligations.add("copyleft-source")
        if "CC-BY-" in expression:
            obligations.add("attribution")
        if "CC-BY-SA-" in expression:
            obligations.add("share-alike")
        role = (
            "notice"
            if path in notice_paths
            else "metadata" if source_id == recipe["generatedSource"]["id"] else "asset"
        )
        member_records.append(
            {
                "licenseExpression": expression,
                "noticePaths": [] if role == "notice" else list(notice_paths),
                "obligations": sorted(obligations),
                "path": path,
                "role": role,
                "sha256": hashlib.sha256(archive.members[path]).hexdigest(),
                "size": len(archive.members[path]),
                "sourceId": source_id,
                "sourcePath": source_path,
                "transformation": transformation,
            }
        )
    return {
        "members": member_records,
        "path": archive.path,
        "sources": source_records,
    }


def build_provenance(
    recipe: dict[str, Any],
    baseline: dict[str, Any],
    archives: Iterable[AssembledArchive],
) -> dict[str, Any]:
    """Return the content-provenance record for one assembled archive set."""
    records = [archive_provenance(recipe, archive) for archive in archives]
    return {
        "$schema": CONTENT_SCHEMA,
        "archives": sorted(records, key=lambda item: item["path"]),
        "baselineIdentity": _canonical_json_identity(baseline),
        "formatVersion": 2,
        "package": {"id": recipe["package"]["id"], "name": recipe["package"]["name"]},
    }


def validate_provenance(provenance: dict[str, Any], baseline: dict[str, Any]) -> None:
    allowed = set(baseline["licensePolicy"]["productInputAllowedExpressions"])
    try:
        validate_content_provenance(
            provenance,
            "generated content provenance",
            allowed_licenses=allowed,
            baseline=baseline,
        )
    except MetadataError as error:
        raise ContentError(str(error)) from error


def subtract_closure(
    archive: ClosureReport, base: ClosureReport, *, keep: Iterable[str] = ()
) -> dict[str, SourceMember]:
    """``closure(M) \\ closure(base)`` — the members only this archive carries.

    Stated as a set difference rather than as "the assets referenced only by
    this map", because the latter is set-dependent: whether an asset is
    referenced only by ``M`` depends on which *other* maps are in the build,
    so adding a map would move an existing archive's bytes.

    ``keep`` survives the subtraction. It is the notice set: every archive is
    published under its own URL and redistributed on its own, so each one
    carries the complete notices even though the base carries them too.
    """
    kept = {_game_path(path) for path in keep}
    return {
        path: member
        for path, member in archive.members.items()
        if path not in base.members or path in kept
    }


def check_duplicate_members(
    archives: dict[str, dict[str, bytes]], *, exempt: Iterable[str] = ()
) -> None:
    """Every member two archives share must be byte-identical.

    One shared ``SourceSet`` already guarantees this for upstream members —
    every game path collapses to exactly one ``SourceMember`` before any
    closure runs — so this is an assertion, not a rule the assembly has to
    obey. It earns its place because ``FS_AddFileToList``
    (ioq3 code/qcommon/files.c) de-duplicates a listing by *file name*, first
    in search-path order winning, so a diverging copy would silently mask the
    other rather than fail.

    ``exempt`` names the members that are generated per archive and therefore
    carry different bytes under the same path on purpose.
    """
    exempted = {_game_path(path) for path in exempt}
    digests: dict[str, tuple[str, str]] = {}
    offenders: list[str] = []
    for archive in sorted(archives):
        for path, data in sorted(archives[archive].items()):
            if _game_path(path) in exempted:
                continue
            digest = hashlib.sha256(data).hexdigest()
            # Keyed the way the engine looks a member up: FS_AddFileToList
            # de-duplicates with Q_stricmp and FS_FOpenFileRead hashes the
            # lower-cased name, so two case variants are one member to it.
            key = _game_path(path)
            previous = digests.get(key)
            if previous is None:
                digests[key] = (archive, digest)
            elif previous[1] != digest:
                offenders.append(
                    f"{path} is sha256:{previous[1]} in {previous[0]} and "
                    f"sha256:{digest} in {archive}"
                )
    if offenders:
        raise ContentError(
            "the same member has different bytes in two archives: "
            + "; ".join(offenders)
        )


def _fs_path_key(name: str) -> tuple[int, ...]:
    """Sort key matching the engine's ``FS_PathCmp``.

    Not ``sorted()``: ``FS_PathCmp`` (ioq3 code/qcommon/files.c) uppercases
    ASCII letters and folds ``\\`` and ``:`` to ``/`` before comparing, so it
    orders ``_`` (0x5F) *after* every letter while Python orders it before every
    lower-case one. PK3 names may contain ``_`` — ``oa_pvomit`` does — so the
    two disagree on exactly the names this pack produces.
    """
    folded = []
    for character in name:
        code = ord(character)
        if 0x61 <= code <= 0x7A:
            code -= 0x20
        elif character in ("\\", ":"):
            code = ord("/")
        folded.append(code)
    return tuple(folded)


def _engine_shader_listing(archives: dict[str, dict[str, bytes]]) -> list[tuple[str, str]]:
    """The `scripts/*.shader` listing the engine would build, in its own order.

    Three engine behaviours decide it, and none of them is the obvious one:

    * ``FS_AddGameDirectory`` sorts PK3s ascending by ``FS_PathCmp`` and
      *prepends* each to the search chain, so ``FS_ListFilteredFiles`` walks
      them **descending**;
    * inside one PK3 the members are walked in ``buildBuffer`` order, which is
      the ZIP's stored order and is *not* re-sorted by the engine — so the
      comparator there is ``write_pk3``'s plain ``sorted()``, not
      ``FS_PathCmp``. The two disagree on ``_`` against a letter, and
      ``scripts/weapon_rocketlauncher.shader`` versus
      ``scripts/weaponry.shader`` is a live pair in the base archive;
    * ``FS_AddFileToList`` keeps only the **first** occurrence of a name,
      case-insensitively, and ``FS_ListFiles`` strips the directory — so a
      shader file two archives both carry appears once, at the position of the
      **highest**-named archive.

    ``ScanAndLoadShaderFiles`` then concatenates the buffers in reverse of this
    listing and ``FindShaderInShaderText`` takes the first hit, so the winner of
    a shader name is the **last** listing entry that defines it.
    """
    listing: list[tuple[str, str]] = []
    seen: set[str] = set()
    for archive in sorted(archives, key=_fs_path_key, reverse=True):
        members = archives[archive]
        files = [
            path
            for path in members
            if path.lower().startswith("scripts/") and path.lower().endswith(".shader")
        ]
        # `write_pk3` stores members with plain `sorted()`, and the engine
        # walks that order as stored.
        for path in sorted(files):
            base_name = path.rsplit("/", 1)[-1].lower()
            if base_name in seen:
                continue
            seen.add(base_name)
            listing.append((archive, path))
    return listing


def check_shader_resolution(
    archives: dict[str, dict[str, bytes]], resolved: dict[str, str]
) -> None:
    """No shader name may resolve differently at run time than in the closure.

    The closure resolves a shader name through one ``ShaderIndex`` over the
    whole source set. At run time the engine resolves it over the *packaged*
    files spread across several archives, in the order
    ``_engine_shader_listing`` reconstructs — which is not the same order, and
    is not even a function of one archive at a time.

    So a name whose index winner sits in a map archive, and which some file
    another archive packages for an unrelated name also defines, would render
    from that other definition and not from the audited one. Nothing reports it.
    This is the check §4.2 asks for, and the archive keys must be the PK3 names
    the engine sees, not the manifest paths.
    """
    winner: dict[str, tuple[str, str]] = {}
    for archive, path in _engine_shader_listing(archives):
        try:
            definitions = parse_shader_file(
                archives[archive][path].decode("latin-1")
            )
        except AssetFormatError as error:  # pragma: no cover - parsed already
            raise ContentError(f"{archive}: {path}: {error}") from error
        for definition in definitions:
            # Later in the listing wins, so a plain assignment is the rule.
            winner[_game_path(definition.name)] = (archive, path)
    offenders = []
    for name, shader_file in sorted(resolved.items()):
        found = winner.get(name)
        if found is None:
            offenders.append(
                f"{name!r} resolves to {shader_file}, which no archive packages"
            )
        elif found[1] != shader_file:
            offenders.append(
                f"{name!r} was closed against {shader_file} but {found[0]} "
                f"defines it in {found[1]}, which wins at run time"
            )
    if offenders:
        raise ContentError(
            "cross-archive shader precedence disagrees with the closure: "
            + "; ".join(offenders)
        )


def iter_forbidden(paths: Iterable[str]) -> list[str]:
    """Return the paths a content pack must never contain."""
    offenders = []
    for path in paths:
        for pattern, reason in FORBIDDEN_MEMBER_PATTERNS:
            if pattern.search(path):
                offenders.append(f"{path} ({reason})")
    return sorted(offenders)
