MAX_AFFECTED_FILES = 20
ASSESSMENT_VERSION = "1.0.0"

RISK_LEVELS = ["none", "low", "medium", "high", "critical"]
RISK_VALUES = dict((level, index) for index, level in enumerate(RISK_LEVELS))

HEADER_SIGNATURE_FIELDS = set(["RSAHEADER", "SIGPGP", "SIGGPG", "DSAHEADER"])
HEADER_METADATA_FIELDS = set([
    "BUILDTIME",
    "BUILDHOST",
    "RPMVERSION",
    "SIGMD5",
    "SHA256HEADER",
    "PAYLOADDIGEST",
])
HEADER_COMPRESSION_FIELDS = set(["PAYLOADCOMPRESSOR", "PAYLOADFLAGS"])
HEADER_IDENTITY_FIELDS = set(["NVRA", "SOURCERPM", "SIZE"])

UNSUPPORTED_PRECISE_TYPES = ["BINARY_CODE", "BINARY_BUILDID"]

SENSITIVE_PATH_PREFIXES = (
    "/bin/",
    "/sbin/",
    "/usr/bin/",
    "/usr/sbin/",
    "/usr/lib/systemd/",
    "/usr/lib64/systemd/",
    "/etc/systemd/",
    "/etc/cron",
    "/etc/pam.d/",
)
HIGHLY_SENSITIVE_PATHS = (
    "/etc/ld.so.preload",
    "/etc/profile",
    "/etc/sudoers",
)

DOC_PATH_PREFIXES = (
    "/usr/share/doc/",
    "/usr/share/info/",
    "/usr/share/licenses/",
    "/usr/share/man/",
)
DEBUG_PATH_PREFIXES = (
    "/usr/lib/debug/",
    "/usr/lib64/debug/",
)
SCRIPT_EXTENSIONS = (
    ".bash",
    ".csh",
    ".fish",
    ".ksh",
    ".lua",
    ".pl",
    ".py",
    ".rb",
    ".sh",
    ".zsh",
)
COMPRESSED_EXTENSIONS = (
    ".bz2",
    ".gz",
    ".lzma",
    ".xz",
    ".zst",
    ".zip",
)


def _risk_at_least(left, right):
    if RISK_VALUES[left] >= RISK_VALUES[right]:
        return left
    return right


def _highest_risk(items):
    risk = "none"
    for item in items:
        risk = _risk_at_least(item.get("risk_level", "none"), risk)
    return risk


def _limited_files(paths):
    paths = sorted(set(paths))
    return paths[:MAX_AFFECTED_FILES], len(paths) > MAX_AFFECTED_FILES


def _new_diff_item(
    diff_type,
    risk_level,
    affected_files=None,
    fields=None,
    possible_diff_types=None,
    expected_environment=False,
    explained=False,
    unexpected=False,
    security_relevant=False,
    needs_deep_analysis=False,
    unresolved=False,
):
    item = {
        "diff_type": diff_type,
        "risk_level": risk_level,
        "_expected_environment": expected_environment,
        "_explained": explained,
        "_unexpected": unexpected,
        "_security_relevant": security_relevant,
        "_needs_deep_analysis": needs_deep_analysis,
        "_unresolved": unresolved,
    }
    if fields:
        item["fields"] = sorted(fields)
    if affected_files:
        limited, truncated = _limited_files(affected_files)
        item["affected_files"] = limited
        item["affected_file_count"] = len(set(affected_files))
        item["affected_files_truncated"] = truncated
    if possible_diff_types:
        item["possible_diff_types"] = possible_diff_types
    return item


def _public_diff_item(item):
    return dict((key, value) for key, value in item.items() if not key.startswith("_"))


def _public_diff_items(items):
    return [_public_diff_item(item) for item in items]


def _affected_count(item):
    return item.get("affected_file_count", 1)


def _is_buildid_path(path):
    return "/.build-id/" in path or path.endswith("/.build-id")


def _is_doc_path(path, item=None):
    if item and item.get("isdoc") == "1":
        return True
    return path.startswith(DOC_PATH_PREFIXES)


def _is_config_path(path, item=None):
    if item and item.get("isconfig") == "1":
        return True
    return path.startswith("/etc/")


def _is_debug_path(path):
    return (
        path.startswith(DEBUG_PATH_PREFIXES)
        or "/.debug/" in path
        or path.endswith(".debug")
        or ".gnu_debug" in path
    )


def _is_script_path(path):
    return (
        path.endswith(SCRIPT_EXTENSIONS)
        or path.startswith("/etc/init.d/")
        or path.startswith("/etc/profile.d/")
    )


def _is_compressed_path(path):
    return path.endswith(COMPRESSED_EXTENSIONS)


def _is_sensitive_path(path):
    return path.startswith(SENSITIVE_PATH_PREFIXES) or path in HIGHLY_SENSITIVE_PATHS


def _mode_int(value):
    try:
        return int(str(value), 8)
    except (TypeError, ValueError):
        return 0


def _has_exec_bit(item):
    return bool(_mode_int(item.get("mode")) & 0o111)


def _has_special_exec_bit(item):
    return bool(_mode_int(item.get("mode")) & 0o6000)


def _is_binary_candidate(path, published, rebuilt):
    if _is_doc_path(path, published) or _is_doc_path(path, rebuilt):
        return False
    if _is_config_path(path, published) or _is_config_path(path, rebuilt):
        return False
    if _is_debug_path(path) or _is_script_path(path):
        return False
    if _has_exec_bit(published) or _has_exec_bit(rebuilt):
        return True
    library_prefixes = ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/")
    return path.startswith(library_prefixes) and (
        ".so" in path or path.endswith((".a", ".o"))
    )


def _path_risk(paths, default_risk="medium"):
    risk = default_risk
    for path in paths:
        if path in HIGHLY_SENSITIVE_PATHS:
            risk = _risk_at_least("critical", risk)
        elif _is_sensitive_path(path):
            risk = _risk_at_least("high", risk)
    return risk


def _paths_security_relevant(paths):
    return any(_is_sensitive_path(path) for path in paths)


def _permission_risk(entries):
    risk = "low"
    for entry in entries:
        if _has_special_exec_bit(entry["rebuilt"]) and not _has_special_exec_bit(entry["published"]):
            return "critical"
        if _has_special_exec_bit(entry["published"]) or _has_special_exec_bit(entry["rebuilt"]):
            risk = _risk_at_least("high", risk)
        elif _is_sensitive_path(entry["path"]):
            risk = _risk_at_least("medium", risk)
    return risk


def _permission_security_relevant(entries):
    for entry in entries:
        if _has_special_exec_bit(entry["published"]) or _has_special_exec_bit(entry["rebuilt"]):
            return True
        if _is_sensitive_path(entry["path"]):
            return True
    return False


def _classify_header_differences(header_diff):
    if not header_diff or not header_diff.get("different"):
        return []

    fields = set(header_diff.get("fields", {}))
    items = []
    signature_fields = fields & HEADER_SIGNATURE_FIELDS
    metadata_fields = fields & HEADER_METADATA_FIELDS
    compression_fields = fields & HEADER_COMPRESSION_FIELDS
    identity_fields = fields & HEADER_IDENTITY_FIELDS
    known_fields = (
        HEADER_SIGNATURE_FIELDS
        | HEADER_METADATA_FIELDS
        | HEADER_COMPRESSION_FIELDS
        | HEADER_IDENTITY_FIELDS
    )
    other_fields = fields - known_fields

    if signature_fields:
        items.append(_new_diff_item(
            "RPM_SIGNATURE",
            "low",
            fields=signature_fields,
            expected_environment=True,
        ))
    if metadata_fields:
        items.append(_new_diff_item(
            "RPM_METADATA",
            "low",
            fields=metadata_fields,
            expected_environment=True,
        ))
    if compression_fields:
        items.append(_new_diff_item(
            "COMPRESSION",
            "none",
            fields=compression_fields,
            explained=True,
        ))
    if identity_fields:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            fields=identity_fields,
            unresolved=True,
        ))
    if other_fields:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            fields=other_fields,
            unresolved=True,
        ))
    return items


def _classify_file_differences(files_diff, file_analysis):
    if not files_diff or files_diff.get("status") != "compared":
        return [_new_diff_item(
            "OTHER",
            "high",
            unresolved=True,
        )]

    if not file_analysis:
        return []

    items = []
    only_in_published = file_analysis["only_in_published"]
    only_in_rebuilt = file_analysis["only_in_rebuilt"]
    buildid_added = [path for path in only_in_rebuilt if _is_buildid_path(path)]
    buildid_removed = [path for path in only_in_published if _is_buildid_path(path)]
    added = [path for path in only_in_rebuilt if not _is_buildid_path(path)]
    removed = [path for path in only_in_published if not _is_buildid_path(path)]

    if buildid_added or buildid_removed:
        items.append(_new_diff_item(
            "BUILDID_SYMLINK",
            "none",
            affected_files=buildid_added + buildid_removed,
            expected_environment=True,
        ))
    if added:
        items.append(_new_diff_item(
            "FILE_ADDED",
            _path_risk(added, "medium"),
            affected_files=added,
            unexpected=True,
            security_relevant=_paths_security_relevant(added),
        ))
    if removed:
        items.append(_new_diff_item(
            "FILE_REMOVED",
            _path_risk(removed, "medium"),
            affected_files=removed,
            unexpected=True,
            security_relevant=_paths_security_relevant(removed),
        ))

    mtime_only = []
    permission_entries = []
    symlink_paths = []
    buildid_symlink_paths = []
    doc_content = []
    config_content = []
    script_content = []
    compressed_content = []
    debug_content = []
    binary_candidates = []
    other_content = []
    other_metadata = []

    for entry in file_analysis["changed"]:
        path = entry["path"]
        fields = set(entry["changed_fields"])
        published = entry["published"]
        rebuilt = entry["rebuilt"]

        if fields == set(["mtime"]):
            mtime_only.append(path)
            continue

        if "linkto" in fields:
            if _is_buildid_path(path):
                buildid_symlink_paths.append(path)
            else:
                symlink_paths.append(path)

        if fields & set(["mode", "owner", "group", "rdev"]):
            permission_entries.append(entry)

        if fields & set(["digest", "size"]):
            if _is_buildid_path(path):
                buildid_symlink_paths.append(path)
            elif _is_debug_path(path):
                debug_content.append(path)
            elif _is_compressed_path(path) and _is_doc_path(path, published):
                compressed_content.append(path)
            elif _is_doc_path(path, published):
                doc_content.append(path)
            elif _is_config_path(path, published):
                config_content.append(path)
            elif _is_script_path(path):
                script_content.append(path)
            elif _is_binary_candidate(path, published, rebuilt):
                binary_candidates.append(path)
            else:
                other_content.append(path)

        metadata_only = fields - set(["mtime", "digest", "size", "mode", "owner", "group", "rdev", "linkto"])
        if metadata_only:
            other_metadata.append(path)

    if mtime_only:
        items.append(_new_diff_item(
            "FILE_TIMESTAMP",
            "none",
            affected_files=mtime_only,
            expected_environment=True,
        ))
    if permission_entries:
        items.append(_new_diff_item(
            "FILE_PERMISSION",
            _permission_risk(permission_entries),
            affected_files=[entry["path"] for entry in permission_entries],
            unexpected=True,
            security_relevant=_permission_security_relevant(permission_entries),
        ))
    if buildid_symlink_paths:
        items.append(_new_diff_item(
            "BUILDID_SYMLINK",
            "none",
            affected_files=buildid_symlink_paths,
            expected_environment=True,
        ))
    if symlink_paths:
        items.append(_new_diff_item(
            "SYMLINK_TARGET",
            _path_risk(symlink_paths, "low"),
            affected_files=symlink_paths,
            explained=not _paths_security_relevant(symlink_paths),
            unexpected=_paths_security_relevant(symlink_paths),
            security_relevant=_paths_security_relevant(symlink_paths),
        ))
    if compressed_content:
        items.append(_new_diff_item(
            "COMPRESSION",
            "low",
            affected_files=compressed_content,
            possible_diff_types=["DOC_CONTENT"],
            unexpected=True,
            needs_deep_analysis=True,
        ))
    if doc_content:
        items.append(_new_diff_item(
            "DOC_CONTENT",
            "low",
            affected_files=doc_content,
            explained=True,
        ))
    if config_content:
        items.append(_new_diff_item(
            "CONFIG_CONTENT",
            _path_risk(config_content, "low"),
            affected_files=config_content,
            unexpected=True,
            security_relevant=_paths_security_relevant(config_content),
        ))
    if script_content:
        items.append(_new_diff_item(
            "SCRIPT_CONTENT",
            _path_risk(script_content, "medium"),
            affected_files=script_content,
            security_relevant=True,
        ))
    if debug_content:
        items.append(_new_diff_item(
            "DEBUG_INFO",
            "low",
            affected_files=debug_content,
            explained=True,
        ))
    if binary_candidates:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            affected_files=binary_candidates,
            possible_diff_types=["BINARY_CODE", "BINARY_BUILDID", "DEBUG_INFO"],
            needs_deep_analysis=True,
            unresolved=True,
        ))
    if other_content:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            affected_files=other_content,
            needs_deep_analysis=True,
            unresolved=True,
        ))
    if other_metadata:
        items.append(_new_diff_item(
            "OTHER",
            "low",
            affected_files=other_metadata,
            unexpected=True,
        ))
    return items


def _classify_dependency_and_scriptlet_differences(requires_equal, provides_equal, scripts_equal):
    items = []
    if not scripts_equal:
        items.append(_new_diff_item(
            "SCRIPT_CONTENT",
            "high",
            security_relevant=True,
        ))
    changed = []
    if not requires_equal:
        changed.append("requires")
    if not provides_equal:
        changed.append("provides")
    if changed:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            fields=changed,
            security_relevant=True,
        ))
    return items


def _summarize_diff_items(items):
    public_items = _public_diff_items(items)
    by_risk = dict((level, 0) for level in RISK_LEVELS)
    by_type = {}
    for item in public_items:
        risk = item.get("risk_level", "none")
        diff_type = item.get("diff_type", "OTHER")
        by_risk[risk] = by_risk.get(risk, 0) + 1
        by_type[diff_type] = by_type.get(diff_type, 0) + 1
    return {
        "total_diff_types": len(by_type),
        "total_diff_items": len(public_items),
        "diff_by_risk_level": by_risk,
        "diff_by_type": by_type,
        "needs_deep_analysis_count": len([
            item for item in items
            if item.get("_needs_deep_analysis")
        ]),
    }


def _unexpected_affected_count(items):
    return sum(
        _affected_count(item)
        for item in items
        if item.get("_unexpected")
    )


def _trust_level(rpm_file_sha256_equal, items):
    if rpm_file_sha256_equal:
        return "L4"

    if any(item.get("_security_relevant") for item in items):
        return "L0"
    if any(item.get("_unresolved") for item in items):
        return "L0"

    non_expected = [
        item for item in items
        if not item.get("_expected_environment")
    ]
    if not non_expected:
        return "L3"

    if all(item.get("_explained") for item in non_expected):
        return "L2"

    if _unexpected_affected_count(items) <= 3:
        return "L1"
    return "L0"


def _action_for_trust_level(trust_level):
    if trust_level in ("L4", "L3"):
        return "approve"
    if trust_level in ("L2", "L1"):
        return "review"
    return "reject"


def _assessment_confidence(trust_level, items):
    if trust_level == "L4":
        return 1.0
    if any(item.get("_unresolved") for item in items):
        return 0.55
    if any(item.get("_needs_deep_analysis") for item in items):
        return 0.6
    if trust_level == "L3":
        return 0.9
    if trust_level == "L2":
        return 0.8
    if trust_level == "L1":
        return 0.7
    return 0.85


def _build_overall_assessment(rpm_file_sha256_equal, items):
    trust_level = _trust_level(rpm_file_sha256_equal, items)
    return {
        "risk_level": "none" if rpm_file_sha256_equal else _highest_risk(items),
        "action": _action_for_trust_level(trust_level),
        "reproducible": bool(rpm_file_sha256_equal),
        "confidence": _assessment_confidence(trust_level, items),
        "trust_level": trust_level,
    }


def _analysis_capabilities():
    return {
        "rpm_header": True,
        "rpm_file_manifest": True,
        "rpm_scriptlet": True,
        "path_based_classification": True,
        "elf_section_compare": False,
        "elf_buildid_compare": False,
        "payload_decompression_compare": False,
    }


def build_light_assessment(
    rpm_file_sha256_equal,
    header_diff,
    files_diff,
    file_analysis,
    requires_equal,
    provides_equal,
    scripts_equal,
):
    internal_items = []
    if not rpm_file_sha256_equal:
        internal_items.extend(_classify_header_differences(header_diff))
        internal_items.extend(_classify_file_differences(files_diff, file_analysis))
        internal_items.extend(_classify_dependency_and_scriptlet_differences(
            requires_equal,
            provides_equal,
            scripts_equal,
        ))

    return {
        "analysis": {
            "mode": "light",
            "capabilities": _analysis_capabilities(),
            "unsupported_precise_types": UNSUPPORTED_PRECISE_TYPES,
        },
        "overall_assessment": _build_overall_assessment(
            rpm_file_sha256_equal,
            internal_items,
        ),
        "diff_items": _public_diff_items(internal_items),
        "summary_stats": _summarize_diff_items(internal_items),
    }
