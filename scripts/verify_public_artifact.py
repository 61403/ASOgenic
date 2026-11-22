"""Export and verify a reviewed file allowlist for a future public MCP tree.

This does not make the current mixed repository publishable. The manifest is a
candidate extraction contract and must be expanded only after each source file
has passed provenance and boundary review.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "release" / "public-artifact.toml"
PRIVATE_PARTS = {
    ".git",
    ".aws",
    ".codex",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "tenancy",
    "web",
    "deploy",
    "phpkit",
    "reference",
    "dist",
    "build",
}
PRIVATE_SUFFIXES = {".p8", ".pem", ".key", ".sqlite", ".sqlite3", ".db", ".log", ".pyc"}
PRIVATE_NAMES = {"server.py", "oauth.py", "id_rsa", "id_ed25519"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{12,}\b"),
    re.compile(r"(?im)^\s*(?:ASC_P8_PEM|APP_STORE_CONNECT_PRIVATE_KEY)\s*=\s*[^\s#]{12,}"),
)


class ArtifactError(Exception):
    """Raised for an invalid manifest or candidate artifact."""


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ArtifactError(f"path must be a safe relative path: {value!r}")
    return path


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactError(f"cannot read manifest {path}: {exc}") from exc
    if data.get("manifest", {}).get("status") not in {"provisional", "approved"}:
        raise ArtifactError("manifest status must be 'provisional' or 'approved'")
    files = data.get("files")
    required = data.get("required", [])
    if not isinstance(files, list) or not files:
        raise ArtifactError("manifest must define a non-empty [[files]] allowlist")
    destinations: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            raise ArtifactError("every [[files]] item must have a source path")
        source = _relative_path(item["source"])
        destination = _relative_path(item.get("destination", str(source)))
        if str(destination) in destinations:
            raise ArtifactError(f"duplicate destination in manifest: {destination}")
        destinations.add(str(destination))
    for value in required:
        if not isinstance(value, str):
            raise ArtifactError("required entries must be path strings")
        _relative_path(value)
    if not set(required).issubset(destinations):
        raise ArtifactError("every required file must also appear in the allowlist")
    return data


def _prohibited(path: PurePosixPath) -> str | None:
    lowered = path.as_posix().lower()
    if any(part.lower() in PRIVATE_PARTS for part in path.parts):
        return "private or generated directory"
    if path.name.lower() in PRIVATE_NAMES:
        return "hosted server or credential module"
    if path.suffix.lower() in PRIVATE_SUFFIXES:
        return "secret, database, log, or generated file type"
    if path.name.lower().startswith((".env", ".ds_store")):
        return "environment or operating-system file"
    if path.name.lower().endswith((".egg-info", ".bak", ".orig", ".swp")):
        return "generated or editor backup file"
    if "credential" in lowered or "vault" in lowered or "billing" in lowered:
        return "sensitive hosted functionality"
    return None


def verify_tree(root: Path, manifest: dict[str, Any]) -> list[str]:
    """Return all violations; an empty list means the tree matches its manifest."""
    if not root.is_dir():
        return [f"candidate root is not a directory: {root}"]
    entries = manifest["files"]
    expected: dict[str, str] = {}
    optional: set[str] = set()
    for item in entries:
        source = str(_relative_path(item["source"]))
        destination = str(_relative_path(item.get("destination", source)))
        expected[destination] = source
        if item.get("optional") is True:
            optional.add(destination)

    problems: list[str] = []
    found: set[str] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        current = Path(directory)
        if current == root:
            # A public repository checkout necessarily has Git's administrative
            # directory. It is not source or artifact content, so exclude it
            # from the tree walk rather than making CI reject every checkout.
            directories[:] = [name for name in directories if name != ".git"]

        for entry in sorted([*directories, *files]):
            path = current / entry
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if path.is_symlink():
                problems.append(f"{relative}: symbolic links are not allowed in a public artifact")
                continue
            violation = _prohibited(relative)
            if violation:
                problems.append(f"{relative}: prohibited {violation}")
            if path.is_dir():
                continue
            if not path.is_file():
                problems.append(f"{relative}: public artifacts may contain only regular files")
                continue
            name = relative.as_posix()
            found.add(name)
            if name not in expected:
                problems.append(f"{name}: not in explicit allowlist")
            try:
                content = path.read_bytes()
            except OSError as exc:
                problems.append(f"{name}: cannot read file ({exc})")
                continue
            if b"\x00" in content:
                problems.append(f"{name}: binary files require separate approval and are not allowed")
                continue
            decoded = content.decode("utf-8", errors="replace")
            for pattern in SECRET_PATTERNS:
                if pattern.search(decoded):
                    problems.append(f"{name}: credential-like content detected")
                    break

    for required in manifest.get("required", []):
        if required not in found:
            problems.append(f"{required}: required public core file is missing")
    for destination, source in expected.items():
        if destination not in found and destination not in optional:
            problems.append(f"{destination}: allowlisted file is missing (source {source})")
    return sorted(set(problems))


def build_gate_problems(root: Path, manifest: dict[str, Any]) -> list[str]:
    """Check reviewed prerequisites before a candidate may be built.

    This is deliberately a preflight only. It does not run a package backend,
    publish artifacts, or treat a local tree scan as approval evidence.
    """
    build = manifest.get("build", {})
    problems: list[str] = []
    if manifest.get("manifest", {}).get("status") != "approved":
        problems.append("manifest.status must be 'approved' after file-by-file review")
    if build.get("status") != "approved":
        problems.append("build.status must be 'approved' by the release owner")
    for approval in (
        "ownership",
        "license",
        "provenance",
        "tree_history_secret_scan",
        "package_manifest_review",
        "local_boundary_review",
    ):
        if build.get("approvals", {}).get(approval) is not True:
            problems.append(f"build.approvals.{approval} requires recorded PASS evidence")

    distribution = build.get("distribution")
    version = build.get("version")
    backend = build.get("build_backend")
    for name, value in (("distribution", distribution), ("version", version), ("build_backend", backend)):
        if not isinstance(value, str) or not value.strip():
            problems.append(f"build.{name} must be set for an approved candidate")

    if root.is_dir():
        problems.extend(verify_tree(root, manifest))
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            problems.append("pyproject.toml: required for package build preflight")
        else:
            try:
                package_config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                problems.append(f"pyproject.toml: cannot parse package metadata ({exc})")
            else:
                project = package_config.get("project", {})
                if project.get("name") != distribution:
                    problems.append("pyproject.toml project.name must match build.distribution")
                if project.get("version") != version:
                    problems.append("pyproject.toml project.version must match build.version")
                backend_config = package_config.get("build-system", {})
                if backend_config.get("build-backend") != backend:
                    problems.append("pyproject.toml build-system.build-backend must match build.build_backend")
        if not (root / "LICENSE").is_file():
            problems.append("LICENSE: approved license text must be present before package build")
    else:
        problems.append(f"candidate root is not a directory: {root}")
    return sorted(set(problems))


def verify_wheel(wheel_path: Path, manifest: dict[str, Any]) -> list[str]:
    """Compare the wheel's Python package inventory with the reviewed allowlist."""
    if not wheel_path.is_file() or wheel_path.suffix != ".whl":
        return [f"wheel is missing or not a .whl file: {wheel_path}"]
    expected = {
        str(_relative_path(item.get("destination", item["source"]))).removeprefix("src/")
        for item in manifest["files"]
        if str(item.get("destination", item["source"])).startswith("src/asogenic/")
    }
    problems: list[str] = []
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            names = set(archive.namelist())
            package_names = {name for name in names if name.startswith("asogenic/")}
            for name in sorted(package_names - expected):
                problems.append(f"wheel includes unallowlisted package file: {name}")
            for name in sorted(expected - package_names):
                problems.append(f"wheel is missing allowlisted package file: {name}")
            for name in sorted(names):
                if name.endswith(".dist-info/RECORD") or ".dist-info/" in name:
                    continue
                reason = _prohibited(PurePosixPath(name))
                if reason:
                    problems.append(f"wheel includes prohibited file {name}: {reason}")
                if name.endswith(".py"):
                    decoded = archive.read(name).decode("utf-8", errors="replace")
                    for pattern in SECRET_PATTERNS:
                        if pattern.search(decoded):
                            problems.append(f"wheel includes credential-like content in {name}")
                            break
    except (OSError, zipfile.BadZipFile) as exc:
        problems.append(f"cannot read wheel archive: {exc}")
    return sorted(set(problems))


def verify_sdist(sdist_path: Path, manifest: dict[str, Any]) -> list[str]:
    """Compare every source file in an sdist with the reviewed candidate allowlist.

    Package backends may add root ``PKG-INFO`` metadata, but no source file outside the candidate
    allowlist is acceptable. This closes the gap where a wheel is clean while an sdist carries
    private source, build configuration, or an accidentally included file.
    """
    if not sdist_path.is_file() or not sdist_path.name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        return [f"sdist is missing or not a supported tar archive: {sdist_path}"]

    expected = {
        str(_relative_path(item.get("destination", item["source"])))
        for item in manifest["files"]
    }
    problems: list[str] = []
    found: set[str] = set()
    try:
        with tarfile.open(sdist_path, "r:*") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
            if len(roots) != 1:
                return [f"sdist must have exactly one top-level directory, found {sorted(roots)}"]
            root = next(iter(roots))
            for member in members:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != root or len(parts) == 1:
                    problems.append(f"sdist has invalid member path {member.name}")
                    continue
                relative = PurePosixPath(*parts[1:])
                name = relative.as_posix()
                if name == "PKG-INFO":
                    continue  # Standard generated source-distribution metadata.
                found.add(name)
                if name not in expected:
                    problems.append(f"sdist includes unallowlisted source file: {name}")
                reason = _prohibited(relative)
                if reason:
                    problems.append(f"sdist includes prohibited file {name}: {reason}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    problems.append(f"sdist cannot read member: {name}")
                    continue
                decoded = extracted.read().decode("utf-8", errors="replace")
                for pattern in SECRET_PATTERNS:
                    if pattern.search(decoded):
                        problems.append(f"sdist includes credential-like content in {name}")
                        break
    except (OSError, tarfile.TarError) as exc:
        return [f"cannot read sdist archive: {exc}"]

    for required in manifest.get("required", []):
        if required not in found:
            problems.append(f"sdist is missing required allowlisted source file: {required}")
    return sorted(set(problems))


def export_tree(source_root: Path, destination_root: Path, manifest: dict[str, Any]) -> None:
    """Copy only listed files into a new candidate directory, then verify it."""
    if destination_root.exists() and any(destination_root.iterdir()):
        raise ArtifactError(f"destination must be empty: {destination_root}")
    files: list[tuple[Path, Path]] = []
    for item in manifest["files"]:
        source_rel = _relative_path(item["source"])
        target_rel = _relative_path(item.get("destination", str(source_rel)))
        source = source_root.joinpath(*source_rel.parts)
        target = destination_root.joinpath(*target_rel.parts)
        if source.is_symlink():
            raise ArtifactError(f"manifest source must be a regular file, not a symbolic link: {source_rel}")
        if not source.is_file():
            if item.get("optional") is True:
                continue
            raise ArtifactError(f"allowlisted source file is missing: {source_rel}")
        reason = _prohibited(target_rel)
        if reason:
            raise ArtifactError(f"manifest allows prohibited target {target_rel}: {reason}")
        files.append((source, target))
    destination_root.mkdir(parents=True, exist_ok=True)
    for source, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    violations = verify_tree(destination_root, manifest)
    if violations:
        raise ArtifactError("exported candidate failed verification:\n" + "\n".join(violations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="verify a candidate tree against the allowlist")
    verify.add_argument("candidate", type=Path)
    export = subparsers.add_parser("export", help="export only allowlisted files from a source tree")
    export.add_argument("source", type=Path)
    export.add_argument("destination", type=Path)
    for command in (verify, export):
        command.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build_check = subparsers.add_parser(
        "build-check", help="check approval and package metadata prerequisites; does not build"
    )
    build_check.add_argument("candidate", type=Path)
    build_check.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build = subparsers.add_parser("build", help="build wheel and sdist after all release gates pass")
    build.add_argument("candidate", type=Path)
    build.add_argument("--outdir", type=Path, required=True)
    build.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    wheel_check = subparsers.add_parser("verify-wheel", help="compare a built wheel with the source allowlist")
    wheel_check.add_argument("candidate", type=Path)
    wheel_check.add_argument("wheel", type=Path)
    wheel_check.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    sdist_check = subparsers.add_parser("verify-sdist", help="compare a built sdist with the source allowlist")
    sdist_check.add_argument("candidate", type=Path)
    sdist_check.add_argument("sdist", type=Path)
    sdist_check.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "export":
            export_tree(args.source, args.destination, manifest)
            candidate = args.destination
        elif args.command in {"build-check", "build"}:
            candidate = args.candidate
            problems = build_gate_problems(candidate, manifest)
            if problems:
                print("Package build preflight refused:", file=sys.stderr)
                print("\n".join(f"- {problem}" for problem in problems), file=sys.stderr)
                return 1
            if args.command == "build-check":
                print(f"Package build prerequisites passed: {candidate} (build not run)")
                return 0
            args.outdir.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--sdist",
                    "--wheel",
                    "--outdir",
                    str(args.outdir.resolve()),
                    str(candidate.resolve()),
                ],
                check=False,
            )
            if completed.returncode:
                return completed.returncode
            print(f"Built wheel and sdist in {args.outdir}")
            return 0
        elif args.command == "verify-wheel":
            problems = verify_tree(args.candidate, manifest) + verify_wheel(args.wheel, manifest)
            if problems:
                print("Wheel verification failed:", file=sys.stderr)
                print("\n".join(f"- {problem}" for problem in sorted(set(problems))), file=sys.stderr)
                return 1
            print(f"Wheel package inventory matches the allowlist: {args.wheel}")
            return 0
        elif args.command == "verify-sdist":
            problems = verify_tree(args.candidate, manifest) + verify_sdist(args.sdist, manifest)
            if problems:
                print("Source-distribution verification failed:", file=sys.stderr)
                print("\n".join(f"- {problem}" for problem in sorted(set(problems))), file=sys.stderr)
                return 1
            print(f"Source-distribution inventory matches the allowlist: {args.sdist}")
            return 0
        else:
            candidate = args.candidate
        problems = verify_tree(candidate, manifest)
    except ArtifactError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("Public artifact verification failed:", file=sys.stderr)
        print("\n".join(f"- {problem}" for problem in problems), file=sys.stderr)
        return 1
    print(f"Candidate matches the reviewed allowlist: {candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
