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
    description,
    affected_files=None,
    fields=None,
    analysis_status="analyzed",
    confidence=0.9,
    possible_diff_types=None,
):
    item = {
        "diff_type": diff_type,
        "risk_level": risk_level,
        "analysis_status": analysis_status,
        "confidence": confidence,
        "description": description,
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
            "RPM signature-related header fields differ.",
            fields=signature_fields,
            confidence=0.95,
        ))
    if metadata_fields:
        items.append(_new_diff_item(
            "RPM_METADATA",
            "low",
            "RPM build metadata or wrapper digests differ.",
            fields=metadata_fields,
            confidence=0.9,
        ))
    if compression_fields:
        items.append(_new_diff_item(
            "COMPRESSION",
            "none",
            "RPM payload compression settings differ.",
            fields=compression_fields,
            confidence=0.9,
        ))
    if identity_fields:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            "RPM identity or package size header fields differ.",
            fields=identity_fields,
            analysis_status="needs_review",
            confidence=0.7,
        ))
    if other_fields:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            "Unclassified RPM header fields differ.",
            fields=other_fields,
            analysis_status="needs_review",
            confidence=0.65,
        ))
    return items


def _classify_file_differences(files_diff, file_analysis):
    if not files_diff or files_diff.get("status") != "compared":
        return [_new_diff_item(
            "OTHER",
            "high",
            "RPM file manifest comparison failed.",
            analysis_status="needs_review",
            confidence=0.5,
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
            ".build-id symlink entries were added or removed.",
            affected_files=buildid_added + buildid_removed,
            confidence=0.85,
        ))
    if added:
        items.append(_new_diff_item(
            "FILE_ADDED",
            _path_risk(added, "medium"),
            "Files exist only in the rebuilt RPM.",
            affected_files=added,
            analysis_status="needs_review",
            confidence=0.75,
        ))
    if removed:
        items.append(_new_diff_item(
            "FILE_REMOVED",
            _path_risk(removed, "medium"),
            "Files exist only in the published RPM.",
            affected_files=removed,
            analysis_status="needs_review",
            confidence=0.75,
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
            "File mtimes differ while content and attributes are equal.",
            affected_files=mtime_only,
            confidence=0.98,
        ))
    if permission_entries:
        items.append(_new_diff_item(
            "FILE_PERMISSION",
            _permission_risk(permission_entries),
            "File mode, owner, group, or device metadata differs.",
            affected_files=[entry["path"] for entry in permission_entries],
            analysis_status="needs_review",
            confidence=0.85,
        ))
    if buildid_symlink_paths:
        items.append(_new_diff_item(
            "BUILDID_SYMLINK",
            "none",
            ".build-id symlink targets or entries differ.",
            affected_files=buildid_symlink_paths,
            confidence=0.8,
        ))
    if symlink_paths:
        items.append(_new_diff_item(
            "SYMLINK_TARGET",
            _path_risk(symlink_paths, "low"),
            "Symlink targets differ.",
            affected_files=symlink_paths,
            analysis_status="needs_review",
            confidence=0.8,
        ))
    if compressed_content:
        items.append(_new_diff_item(
            "COMPRESSION",
            "low",
            "Compressed documentation differs; light mode did not decompress logical content.",
            affected_files=compressed_content,
            analysis_status="needs_deep_analysis",
            confidence=0.4,
            possible_diff_types=["COMPRESSION", "DOC_CONTENT"],
        ))
    if doc_content:
        items.append(_new_diff_item(
            "DOC_CONTENT",
            "low",
            "Documentation content differs.",
            affected_files=doc_content,
            confidence=0.8,
        ))
    if config_content:
        items.append(_new_diff_item(
            "CONFIG_CONTENT",
            _path_risk(config_content, "low"),
            "Configuration file content differs.",
            affected_files=config_content,
            analysis_status="needs_review",
            confidence=0.8,
        ))
    if script_content:
        items.append(_new_diff_item(
            "SCRIPT_CONTENT",
            _path_risk(script_content, "medium"),
            "Packaged script file content differs.",
            affected_files=script_content,
            analysis_status="needs_review",
            confidence=0.75,
        ))
    if debug_content:
        items.append(_new_diff_item(
            "DEBUG_INFO",
            "low",
            "Debug information content differs.",
            affected_files=debug_content,
            confidence=0.75,
        ))
    if binary_candidates:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            "Executable or library content differs; light mode did not inspect ELF sections or BuildID.",
            affected_files=binary_candidates,
            analysis_status="needs_deep_analysis",
            confidence=0.35,
            possible_diff_types=["BINARY_CODE", "BINARY_BUILDID", "DEBUG_INFO"],
        ))
    if other_content:
        items.append(_new_diff_item(
            "OTHER",
            "medium",
            "File content differs and was not classified by light rules.",
            affected_files=other_content,
            analysis_status="needs_deep_analysis",
            confidence=0.35,
        ))
    if other_metadata:
        items.append(_new_diff_item(
            "OTHER",
            "low",
            "RPM file flags differ and were not classified by light rules.",
            affected_files=other_metadata,
            analysis_status="needs_review",
            confidence=0.6,
        ))
    return items


def _classify_dependency_and_scriptlet_differences(requires_equal, provides_equal, scripts_equal):
    items = []
    if not scripts_equal:
        items.append(_new_diff_item(
            "SCRIPT_CONTENT",
            "high",
            "RPM scriptlets differ.",
            analysis_status="needs_review",
            confidence=0.9,
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
            "RPM dependency metadata differs.",
            fields=changed,
            analysis_status="needs_review",
            confidence=0.75,
        ))
    return items


def _summarize_diff_items(diff_items):
    by_risk = dict((level, 0) for level in RISK_LEVELS)
    by_type = {}
    for item in diff_items:
        risk = item.get("risk_level", "none")
        diff_type = item.get("diff_type", "OTHER")
        by_risk[risk] = by_risk.get(risk, 0) + 1
        by_type[diff_type] = by_type.get(diff_type, 0) + 1
    return {
        "total_diff_types": len(by_type),
        "total_diff_items": len(diff_items),
        "diff_by_risk_level": by_risk,
        "diff_by_type": by_type,
        "needs_deep_analysis_count": len([
            item for item in diff_items
            if item.get("analysis_status") == "needs_deep_analysis"
        ]),
        "needs_review_count": len([
            item for item in diff_items
            if item.get("analysis_status") == "needs_review"
        ]),
    }


def _assessment_confidence(diff_items):
    if not diff_items:
        return 1.0
    return round(min(item.get("confidence", 0.5) for item in diff_items), 2)


def _build_overall_assessment(rpm_file_sha256_equal, diff_items):
    if rpm_file_sha256_equal:
        return {
            "risk_level": "none",
            "action": "pass",
            "reproducible": True,
            "confidence": 1.0,
            "trust_level": "L4",
            "conclusion": "Published and rebuilt RPM files are byte-identical.",
        }

    highest = _highest_risk(diff_items)
    has_deep_gap = any(
        item.get("analysis_status") == "needs_deep_analysis"
        for item in diff_items
    )
    has_review_gap = any(
        item.get("analysis_status") == "needs_review"
        for item in diff_items
    )

    if highest in ("critical", "high"):
        trust_level = "L0"
        action = "block"
        conclusion = "High-risk or security-sensitive differences were found."
    elif highest == "medium":
        trust_level = "L1"
        action = "review"
        conclusion = "Unexpected or unresolved medium-risk differences were found."
    elif has_deep_gap or has_review_gap:
        trust_level = "L1"
        action = "review"
        conclusion = "Low-risk differences need review because light analysis could not fully explain them."
    else:
        trust_level = "L3"
        action = "pass"
        conclusion = "Only expected rebuild wrapper or timestamp differences were found by light analysis."

    return {
        "risk_level": highest,
        "action": action,
        "reproducible": False,
        "confidence": _assessment_confidence(diff_items),
        "trust_level": trust_level,
        "conclusion": conclusion,
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
    diff_items = []
    if not rpm_file_sha256_equal:
        diff_items.extend(_classify_header_differences(header_diff))
        diff_items.extend(_classify_file_differences(files_diff, file_analysis))
        diff_items.extend(_classify_dependency_and_scriptlet_differences(
            requires_equal,
            provides_equal,
            scripts_equal,
        ))

    return {
        "analysis_mode": "light",
        "analysis_capabilities": _analysis_capabilities(),
        "unsupported_precise_types": UNSUPPORTED_PRECISE_TYPES,
        "overall_assessment": _build_overall_assessment(
            rpm_file_sha256_equal,
            diff_items,
        ),
        "diff_items": diff_items,
        "summary_stats": _summarize_diff_items(diff_items),
    }
