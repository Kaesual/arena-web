# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic assembly of the audited arena-web content pack.

The pack is not a curated file list: it is the transitive closure of what the
pinned ioquake3 `baseq3` QVM sources reference, plus the one map, one player
presentation and bot data named by the committed recipe. Every member is read
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
    """Expand every required reference of the profile into concrete members."""

    def __init__(self, sources: SourceSet, recipe: dict[str, Any]) -> None:
        self.sources = sources
        self.recipe = recipe
        self.shaders = ShaderIndex(sources)
        self.report = ClosureReport()
        self._seen: set[tuple[str, str]] = set()
        self._accepted = {
            _game_path(entry["reference"]): entry["reason"]
            for entry in recipe.get("acceptedUnresolved", [])
        }
        self._accepted_hit: set[str] = set()
        self._generated = {
            _game_path(path) for path in recipe.get("generatedMembers", ())
        }
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
        found = self.shaders.lookup(stripped) or self.shaders.lookup(reference)
        if found is not None:
            shader_file, images = found
            self._take(shader_file, f"shader {reference}")
            self.report.shader_files.add(shader_file)
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
    if recipe.get("formatVersion") != 1:
        _fail(str(path), "has an unsupported formatVersion")
    # derivedReferences is required, not optional: without it the builder would
    # silently drop closure root 1b and emit a smaller pack with a clean exit,
    # which is exactly the fail-open shape the category exists to close.
    for key in (
        "package",
        "packPath",
        "profile",
        "sources",
        "notices",
        "derivedReferences",
    ):
        _require(recipe, key, str(path))
    if not recipe["sources"]:
        _fail(f"{path}.sources", "must not be empty")
    return recipe


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


def provenance_sources(
    recipe: dict[str, Any], used_source_ids: set[str]
) -> list[dict[str, Any]]:
    """Return the sorted provenance source records the pack actually used."""
    records = []
    for record in list(recipe["sources"]) + [recipe["generatedSource"]]:
        if record["id"] not in used_source_ids:
            continue
        records.append(
            {
                "id": record["id"],
                "licenseEvidenceUrl": record["licenseEvidenceUrl"],
                "licenseExpression": record["licenseExpression"],
                "preferredSourceRevision": record["preferredSourceRevision"],
                "preferredSourceUrl": record["preferredSourceUrl"],
                "sourceIdentity": record["sourceIdentity"],
                "sourceUrl": record["sourceUrl"],
            }
        )
    return sorted(records, key=lambda item: item["id"])


def build_provenance(
    recipe: dict[str, Any],
    baseline: dict[str, Any],
    members: dict[str, bytes],
    origins: dict[str, tuple[str, str, str]],
) -> dict[str, Any]:
    """Return the content-provenance record for one assembled pack."""
    notice_paths = tuple(sorted(recipe["notices"]))
    missing_notices = [path for path in notice_paths if path not in members]
    if missing_notices:
        raise ContentError(
            f"declared notice members are not packaged: {missing_notices}"
        )
    source_records = provenance_sources(recipe, {origins[path][0] for path in members})
    licenses = {record["id"]: record["licenseExpression"] for record in source_records}

    member_records = []
    for path in sorted(members):
        source_id, source_path, transformation = origins[path]
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
                "sha256": hashlib.sha256(members[path]).hexdigest(),
                "size": len(members[path]),
                "sourceId": source_id,
                "sourcePath": source_path,
                "transformation": transformation,
            }
        )
    return {
        "$schema": CONTENT_SCHEMA,
        "baselineIdentity": _canonical_json_identity(baseline),
        "formatVersion": 1,
        "members": member_records,
        "package": {"id": recipe["package"]["id"], "name": recipe["package"]["name"]},
        "sources": source_records,
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


def iter_forbidden(paths: Iterable[str]) -> list[str]:
    """Return the paths a content pack must never contain."""
    offenders = []
    for path in paths:
        for pattern, reason in FORBIDDEN_MEMBER_PATTERNS:
            if pattern.search(path):
                offenders.append(f"{path} ({reason})")
    return sorted(offenders)
