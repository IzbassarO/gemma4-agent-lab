"""Static validator for a competition agent bundle (source dir or extracted ZIP). Never writes anything.

Usage:
    uv run python -m tools.validate_submission agents/candidates/<id>          # human-readable
    uv run python -m tools.validate_submission agents/candidates/<id> --json   # machine-readable (stdout)

Exit status: 0 = STRUCTURAL_VALID, 1 = STRUCTURAL_INVALID (including any unprocessable input; never a traceback).
Three kinds of findings, kept separate:
  errors                 hard structural failures (block packaging)
  warnings               files excluded from packaging; undocumented-but-not-forbidden fields
  certification_issues   behavior that depends on an UNCERTIFIED harness (H-IDs); never a pass/fail verdict
`harness_compatibility` is CURRENT_HARNESS_COMPATIBILITY_UNKNOWN until H23 (harness capture) is resolved.

This is OUR static implementation of the contract documented in the local HARNESS_README (sha256 3d6e57a1...,
sections 2.2-2.4 and 3.2). It is not derived from adk-submission source, which is not available locally (H23), and
makes no claim of equivalence with the current compiler. Constraints marked "repo policy" are stricter than README.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from tools import safetensors_check

# ---- documented contract (HARNESS_README) --------------------------------------------------------
ALLOWED_MODEL = "gemma-4-31b-it-qat-w4a16-ct"
MODEL_PREFIXES = ("openai/", "google/", "hosted_vllm/", "custom/")
ROOT_CONFIG_NAMES = ("agent.yaml", "agent.yml", "root_agent.yaml", "root_agent.yml")
BUILTIN_TOOLS = ("run_command", "submit_patch", "get_status", "read_file", "edit_file", "write_file",
                 "get_code_neighbors", "search_similar_code", "get_code_subgraph")
AGENT_CLASSES = ("LlmAgent", "SequentialAgent", "ParallelAgent", "LoopAgent")
LLM_FIELDS = {"agent_class", "name", "model", "adapter", "description", "instruction", "global_instruction", "tools",
              "skills", "sub_agents", "output_key", "include_contents", "disallow_transfer_to_parent",
              "disallow_transfer_to_peers", "generate_content_config"}
WORKFLOW_FIELDS = {"agent_class", "name", "description", "sub_agents"}
LOOP_FIELDS = WORKFLOW_FIELDS | {"max_iterations"}
INCLUDE_CONTENTS = ("default", "none")
GEN_FIELDS = {"temperature", "top_p", "top_k", "max_output_tokens", "presence_penalty", "frequency_penalty",
              "stop_sequences", "response_mime_type", "seed", "thinking_config"}
FORBIDDEN_GEN_FIELDS = ("tools", "system_instruction", "http_options", "safety_settings", "response_schema")
THINKING_FIELDS = {"thinking_budget", "thinking_level", "include_thoughts"}
THINKING_LEVELS = ("MINIMAL", "LOW", "MEDIUM", "HIGH", "NONE")  # case-insensitive
ALLOWED_EXTENSIONS = (".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors")
MAX_TOTAL_BYTES = 3_221_225_472  # strict "<"
MAX_FILES = 10_000
MAX_YAML_FILES = 1_000
MAX_YAML_BYTES = 50 * 1024 * 1024  # per YAML file and per cumulative !include expansion
MAX_SKILL_BYTES = 50 * 1024 * 1024
MAX_INSTRUCTION_CHARS = 1_000_000
MAX_TOTAL_INSTRUCTION_CHARS = 10_000_000
MAX_AGENTS = 500
MAX_DEPTH = 50
MAX_SKILLS = 1_000
LOOP_RANGE = (1, 500)
MAX_INCLUDE_DEPTH = 10
MAX_OUTPUT_TOKENS = (1, 32768)
THINKING_BUDGET = (0, 32768)

# ---- repo policy / validator safety --------------------------------------------------------------
PACKAGED_DIRS = ("configs", "prompts", "sub_agents", "skills", "adapters")
ROOT_PACKAGED_FILES = ("eval_config.yaml",)
PLACEHOLDER_MARKER = "TEMPLATE_PLACEHOLDER"
MAX_EXPANSION_NODES = 1_000_000  # validator self-protection against alias bombs (not a documented harness limit)
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep", ".gitignore"}
JUNK_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints", ".git", ".venv", "venv"}
JUNK_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp", "~")
SECRET_RE = re.compile(r"^(\.env(\..*)?|.*\.pem|.*\.key|kaggle\.json|credentials.*\.json|id_(rsa|ed25519|ecdsa)(\.pub)?|\.netrc|\.pypirc)$", re.I)
SECRET_DIRS = {"secrets", ".kaggle", ".ssh", ".aws"}
DATASET_NAMES = {"snapshots", "graphs", "embeddings", "wheels", "tasks.jsonl", "gemma-4-developer-agent"}
DATASET_SUFFIXES = (".tgz", ".npz", ".whl", ".parquet", ".jsonl")

STRUCTURAL_VALID, STRUCTURAL_INVALID = "STRUCTURAL_VALID", "STRUCTURAL_INVALID"
COMPAT_UNKNOWN = "CURRENT_HARNESS_COMPATIBILITY_UNKNOWN"


@dataclass(frozen=True)
class Issue:
    code: str
    path: str  # bundle-relative POSIX path ('' = bundle root); never absolute
    message: str
    h_id: str | None = None


@dataclass
class Report:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    certification_issues: list[Issue] = field(default_factory=list)
    root_config: str | None = None
    packaged_files: list[str] = field(default_factory=list)
    excluded_files: list[str] = field(default_factory=list)
    unpacked_bytes: int = 0
    models: list[str] = field(default_factory=list)
    adapters_referenced: list[str] = field(default_factory=list)
    adapters_present: list[str] = field(default_factory=list)
    agent_count: int = 0
    max_depth: int = 0

    @property
    def structural_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        def uniq(xs):
            d = {(i.code, i.path, i.message): asdict(i) for i in xs}
            return [d[k] for k in sorted(d)]
        return {
            "structural": STRUCTURAL_VALID if self.structural_valid else STRUCTURAL_INVALID,
            "harness_compatibility": COMPAT_UNKNOWN,
            "harness_compatibility_reason": "H23 harness capture missing; checks implement HARNESS_README documentation, not compiler source",
            "root_config": self.root_config,
            "errors": uniq(self.errors),
            "warnings": uniq(self.warnings),
            "certification_issues": uniq(self.certification_issues),
            "packaged_files": sorted(self.packaged_files),
            "excluded_files": sorted(self.excluded_files),
            "unpacked_bytes": self.unpacked_bytes,
            "models": sorted(set(self.models)),
            "adapters_referenced": sorted(set(self.adapters_referenced)),
            "adapters_present": sorted(set(self.adapters_present)),
            "agent_count": self.agent_count,
            "max_agent_depth": self.max_depth,
        }


def normalize_model(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for p in MODEL_PREFIXES:
            if name.startswith(p):
                name, changed = name[len(p):], True
    return name


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---- YAML loading with !include kept symbolic -------------------------------------------------

class IncludeRef:
    def __init__(self, value):
        self.value = value


class _Loader(yaml.SafeLoader):
    pass


def _include(loader, node):
    return IncludeRef(loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None)


def _mapping(loader, node, deep=False):
    keys = []
    for k_node, _ in node.value:
        k = loader.construct_object(k_node, deep=deep)
        try:
            hash(k)
        except TypeError:
            raise yaml.constructor.ConstructorError(None, None, "unhashable mapping key", k_node.start_mark)
        if k in keys:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {k!r}", k_node.start_mark)
        keys.append(k)
    return loader.construct_mapping(node, deep=deep)


_Loader.add_constructor("!include", _include)
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


class _Abort(Exception):
    """Stop expanding one document after a fatal structured error has been recorded."""


class Bundle:
    """Resolves references inside one bundle directory and records issues on a Report."""

    def __init__(self, root: Path, report: Report, files: set[str], sizes: dict[str, int]):
        self.root, self.r, self.files, self.sizes = root, report, files, sizes
        self.cache: dict[str, object] = {}
        self.referenced: set[str] = set()

    # HARNESS_README does not say what a relative !include / config_path is relative to. We try the including
    # file's directory first (the only reading under which the official sample is self-consistent), then the bundle
    # root, and report any case where the two readings differ (H26).
    def resolve_ref(self, value, from_file: str, kind: str) -> str | None:
        where = from_file
        if not isinstance(value, str) or not value.strip():
            self.r.errors.append(Issue(f"{kind}_INVALID", where, f"{kind.lower()} target must be a non-empty string"))
            return None
        if "\0" in value or "\\" in value:
            self.r.errors.append(Issue(f"{kind}_INVALID", where, f"{kind.lower()} target contains NUL or backslash"))
            return None
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or value.startswith("~"):
            self.r.errors.append(Issue(f"{kind}_ABSOLUTE", where, f"absolute {kind.lower()} {value!r}"))
            return None
        base = posixpath.dirname(from_file)
        file_rel = posixpath.normpath(posixpath.join(base, value))
        root_rel = posixpath.normpath(value)
        has_parent = ".." in value.split("/")
        if has_parent:
            self.r.certification_issues.append(Issue(
                "H26_UNRESOLVED_PARENT_INCLUDE", where,
                f"{kind.lower()} {value!r} uses '..': README says '..' is blocked, the official sample uses it; "
                "current compiler behavior NOT-REPRODUCED", "H26"))
        if file_rel == ".." or file_rel.startswith("../"):
            self.r.errors.append(Issue(f"{kind}_ESCAPES_ROOT", where, f"{kind.lower()} {value!r} resolves outside the bundle"))
            return None
        candidates = [file_rel] if (has_parent or base == "") else [file_rel, root_rel]
        existing = [c for c in candidates if c in self.files]
        if not existing:
            self.r.errors.append(Issue(f"{kind}_MISSING", where, f"{kind.lower()} {value!r} not found (tried {', '.join(sorted(set(candidates)))})"))
            return None
        if len(candidates) == 2 and candidates[0] != candidates[1]:
            if len(existing) == 2:
                self.r.certification_issues.append(Issue(
                    "H26_REFERENCE_BASE_AMBIGUOUS", where,
                    f"{kind.lower()} {value!r} exists relative to the file ({file_rel}) and to the bundle root ({root_rel}); "
                    "which one the harness uses is HOST-UNKNOWN", "H26"))
            else:
                how = "relative to the including file" if existing[0] == file_rel else "relative to the bundle root"
                self.r.certification_issues.append(Issue(
                    "H26_REFERENCE_BASE_UNCERTIFIED", where,
                    f"{kind.lower()} {value!r} only resolves {how} ({existing[0]}); the harness's base directory is HOST-UNKNOWN", "H26"))
        self.referenced.add(existing[0])
        return existing[0]

    def load(self, rel: str):
        """Top-level load of one YAML document with includes expanded (text for .md/.txt, parsed for .yaml/.yml)."""
        if rel in self.cache:
            return self.cache[rel]
        self._budget = {"bytes": 0, "nodes": 0}
        try:
            out = self._load(rel, ())
        except _Abort:
            out = None
        if self._budget["bytes"] > MAX_YAML_BYTES:
            self.r.errors.append(Issue("YAML_EXPANSION_TOO_LARGE", rel, f"cumulative !include expansion {self._budget['bytes']} bytes > {MAX_YAML_BYTES}"))
        self.cache[rel] = out
        return out

    def _load(self, rel: str, stack: tuple[str, ...]):
        if rel in stack:
            self.r.errors.append(Issue("INCLUDE_CYCLE", rel, " -> ".join(stack + (rel,))))
            raise _Abort
        if len(stack) > MAX_INCLUDE_DEPTH:
            self.r.errors.append(Issue("INCLUDE_DEPTH", rel, f"include depth > {MAX_INCLUDE_DEPTH}"))
            raise _Abort
        self._budget["bytes"] += self.sizes.get(rel, 0)
        try:
            data = yaml.load((self.root / rel).read_text(encoding="utf-8"), Loader=_Loader)
        except UnicodeDecodeError:
            self.r.errors.append(Issue("YAML_INVALID", rel, "not valid UTF-8"))
            raise _Abort
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
            problem = getattr(e, "problem", None) or type(e).__name__
            code = "YAML_RECURSIVE_ALIAS" if "recursive" in problem else "YAML_INVALID"
            self.r.errors.append(Issue(code, rel, f"{problem}{where}"))
            raise _Abort
        except RecursionError:
            self.r.errors.append(Issue("YAML_RECURSIVE_ALIAS", rel, "self-referencing YAML structure"))
            raise _Abort
        return self._expand(data, rel, stack + (rel,), set())

    def _expand(self, node, rel: str, stack: tuple[str, ...], active: set[int]):
        self._budget["nodes"] += 1
        if self._budget["nodes"] > MAX_EXPANSION_NODES:
            self.r.errors.append(Issue("YAML_EXPANSION_LIMIT", rel, f"more than {MAX_EXPANSION_NODES} nodes after alias/include expansion"))
            raise _Abort
        if isinstance(node, IncludeRef):
            target = self.resolve_ref(node.value, rel, "INCLUDE")
            if target is None:
                return None
            if target.endswith((".md", ".txt")):
                self._budget["bytes"] += self.sizes.get(target, 0)
                try:
                    return (self.root / target).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    self.r.errors.append(Issue("INCLUDE_NOT_UTF8", target, "included text is not valid UTF-8"))
                    return None
            if target.endswith((".yaml", ".yml")):
                return self._load(target, stack)
            self.r.errors.append(Issue("INCLUDE_UNSUPPORTED_TYPE", rel, f"cannot include {target} (only .md .txt .yaml .yml)"))
            return None
        if isinstance(node, (dict, list)):
            if id(node) in active:
                self.r.errors.append(Issue("YAML_RECURSIVE_ALIAS", rel, "self-referencing YAML alias"))
                raise _Abort
            active.add(id(node))
            try:
                if isinstance(node, dict):
                    return {k: self._expand(v, rel, stack, active) for k, v in node.items()}
                return [self._expand(v, rel, stack, active) for v in node]
            finally:
                active.discard(id(node))
        return node


# ---- agent schema ------------------------------------------------------------------------------

class _Walk:
    def __init__(self, b: Bundle, adapters_ok: set[str]):
        self.b, self.r, self.adapters_ok = b, b.r, adapters_ok
        self.instruction_total = 0
        self.skills = 0
        self.over_limit = False

    def err(self, code, where, msg):
        self.r.errors.append(Issue(code, where, msg))

    def agent(self, cfg, where: str, depth: int, files: tuple[str, ...]) -> None:
        r = self.r
        if self.over_limit:
            return
        r.agent_count += 1
        r.max_depth = max(r.max_depth, depth)
        if r.agent_count > MAX_AGENTS:
            self.err("TOO_MANY_AGENTS", where, f"more than {MAX_AGENTS} agents in the compiled tree")
            self.over_limit = True
            return
        if depth > MAX_DEPTH:
            self.err("AGENT_DEPTH_EXCEEDED", where, f"nesting depth {depth} > {MAX_DEPTH}")
            return
        if not isinstance(cfg, dict):
            self.err("AGENT_CONFIG_INVALID", where, f"agent config must be a mapping, got {type(cfg).__name__}")
            return
        name = cfg.get("name")
        if not isinstance(name, str) or not name.strip():
            self.err("AGENT_NAME_MISSING", where, "every agent needs a non-empty string `name`")
        cls = cfg.get("agent_class", "LlmAgent")
        if cls not in AGENT_CLASSES:
            self.err("AGENT_CLASS_UNSUPPORTED", where, f"agent_class {cls!r} not in {AGENT_CLASSES}")
            return
        allowed = LLM_FIELDS if cls == "LlmAgent" else LOOP_FIELDS if cls == "LoopAgent" else WORKFLOW_FIELDS
        for k in sorted(set(cfg) - allowed, key=str):
            r.warnings.append(Issue("AGENT_FIELD_UNDOCUMENTED", where, f"{cls} field {k!r} is not documented in HARNESS_README"))
        desc = cfg.get("description")
        if desc is not None and not isinstance(desc, str):
            self.err("AGENT_FIELD_TYPE", where, "description must be a string")
        elif isinstance(desc, str) and len(desc) > MAX_INSTRUCTION_CHARS:
            self.err("DESCRIPTION_TOO_LONG", where, f"description {len(desc)} chars > {MAX_INSTRUCTION_CHARS}")
        if cls == "LlmAgent":
            self.llm(cfg, where, depth, files)
        elif cls == "LoopAgent":
            mi = cfg.get("max_iterations", 500)
            if not _is_int(mi) or not LOOP_RANGE[0] <= mi <= LOOP_RANGE[1]:
                self.err("LOOP_MAX_ITERATIONS_INVALID", where, f"max_iterations={mi!r} must be an int in {LOOP_RANGE[0]}..{LOOP_RANGE[1]}")
        self.sub_agents(cfg.get("sub_agents"), where, depth, files)

    def llm(self, cfg: dict, where: str, depth: int, files: tuple[str, ...]) -> None:
        r = self.r
        model = cfg.get("model")
        if not isinstance(model, str) or not model:
            self.err("MODEL_NOT_DECLARED", where, "every LlmAgent must declare `model` as a string (repo policy)")
        else:
            r.models.append(normalize_model(model))
        for k in ("instruction", "global_instruction"):
            v = cfg.get(k)
            if v is None:
                continue
            if not isinstance(v, str):
                self.err("AGENT_FIELD_TYPE", where, f"{k} must be a string (got {type(v).__name__})")
            elif k == "instruction":
                self.instruction_total += len(v)
                if len(v) > MAX_INSTRUCTION_CHARS:
                    self.err("INSTRUCTION_TOO_LONG", where, f"instruction {len(v)} chars > {MAX_INSTRUCTION_CHARS}")
        for k in ("adapter", "output_key"):
            if cfg.get(k) is not None and not isinstance(cfg[k], str):
                self.err("AGENT_FIELD_TYPE", where, f"{k} must be a string or null")
        for k in ("disallow_transfer_to_parent", "disallow_transfer_to_peers"):
            if cfg.get(k) is not None and not isinstance(cfg[k], bool):
                self.err("AGENT_FIELD_TYPE", where, f"{k} must be a boolean or null")
        if "include_contents" in cfg and cfg["include_contents"] not in INCLUDE_CONTENTS:
            self.err("INCLUDE_CONTENTS_INVALID", where, f"include_contents={cfg['include_contents']!r} not in {INCLUDE_CONTENTS}")
        adapter = cfg.get("adapter")
        if isinstance(adapter, str):
            r.adapters_referenced.append(adapter)
            if adapter not in self.adapters_ok:
                self.err("ADAPTER_MISSING", where, f"adapter {adapter!r} has no adapters/{adapter}/ (config + safetensors) or adapters/{adapter}.safetensors")
        self.generation(cfg.get("generate_content_config"), where)
        self.tools(cfg.get("tools"), cfg.get("name"), where, depth, files)
        skills = cfg.get("skills")
        if skills is not None and not (isinstance(skills, list) and all(isinstance(s, str) for s in skills)):
            self.err("SKILLS_INVALID", where, "skills must be a list of strings")
        for s in skills if isinstance(skills, list) else []:
            if not isinstance(s, str):
                continue
            self.skills += 1
            target = posixpath.normpath(s)
            if target.startswith("..") or target.startswith("/") or f"{target}/SKILL.md" not in self.b.files:
                self.err("SKILL_MISSING", where, f"skill {s!r} has no {target}/SKILL.md inside the bundle")
            else:
                size = sum(self.b.sizes[f] for f in self.b.files if f.startswith(target + "/"))
                if size > MAX_SKILL_BYTES:
                    self.err("SKILL_TOO_LARGE", where, f"skill {s!r} is {size} bytes > {MAX_SKILL_BYTES}")

    def tools(self, tools, agent_name, where: str, depth: int, files: tuple[str, ...]) -> None:
        if tools is None:
            tools = []
        if not isinstance(tools, list):
            self.err("TOOLS_INVALID", where, "tools must be a list")
            return
        declared = set()
        for t in tools:
            if isinstance(t, str):
                declared.add(t)
                if t not in BUILTIN_TOOLS:
                    self.err("UNKNOWN_TOOL", where, f"tool {t!r} is not one of the 9 built-in tools")
            elif isinstance(t, dict) and "agent_tool" in t:
                at = t["agent_tool"]
                if len(t) != 1:
                    self.err("TOOL_ENTRY_INVALID", where, "an agent_tool entry must contain only the `agent_tool` key")
                if not isinstance(at, dict):
                    self.err("AGENT_TOOL_INVALID", where, f"agent_tool must be a mapping with config_path, got {type(at).__name__}")
                    continue
                for k in sorted(set(at) - {"config_path", "skip_summarization"}, key=str):
                    self.r.warnings.append(Issue("AGENT_FIELD_UNDOCUMENTED", where, f"agent_tool field {k!r} is not documented"))
                if "skip_summarization" in at and not isinstance(at["skip_summarization"], bool):
                    self.err("AGENT_TOOL_INVALID", where, "agent_tool.skip_summarization must be a boolean")
                if "config_path" not in at:
                    self.err("AGENT_TOOL_INVALID", where, "agent_tool needs config_path")
                    continue
                self.r.certification_issues.append(Issue("H20_AGENT_TOOL_UNCERTIFIED", where,
                                                         f"agent_tool (skip_summarization={at.get('skip_summarization')!r}) behavior NOT-REPRODUCED", "H20"))
                self.follow(at["config_path"], where, depth + 1, files)
            elif isinstance(t, dict):
                self.agent(t, where, depth + 1, files)  # inline agent
            else:
                self.err("TOOL_ENTRY_INVALID", where, f"unsupported tools entry of type {type(t).__name__}")
        missing = sorted(set(BUILTIN_TOOLS) - declared)
        if missing:
            self.r.certification_issues.append(Issue("H04_H30_TOOL_SUBSET", where,
                                                     f"agent {agent_name!r} does not declare {missing}; undeclared/advertised tool behavior NOT-REPRODUCED", "H04"))

    def sub_agents(self, subs, where: str, depth: int, files: tuple[str, ...]) -> None:
        if subs is None:
            return
        if not isinstance(subs, list):
            self.err("SUB_AGENTS_INVALID", where, "sub_agents must be a list")
            return
        for s in subs:
            if isinstance(s, dict) and "config_path" in s:
                if len(s) != 1:
                    self.err("SUB_AGENTS_INVALID", where, "a config_path sub_agent entry must contain only `config_path`")
                self.follow(s["config_path"], where, depth + 1, files)
            elif isinstance(s, dict):
                self.agent(s, where, depth + 1, files)
            else:
                self.err("SUB_AGENTS_INVALID", where, f"unsupported sub_agents entry of type {type(s).__name__}")

    def follow(self, config_path, where: str, depth: int, files: tuple[str, ...]) -> None:
        target = self.b.resolve_ref(config_path, where, "CONFIG_PATH")
        if target is None:
            return
        if target in files:
            self.err("AGENT_REFERENCE_CYCLE", where, " -> ".join(files + (target,)))
            return
        if not target.endswith((".yaml", ".yml")):
            self.err("CONFIG_PATH_INVALID", where, f"config_path {config_path!r} is not a YAML file")
            return
        self.agent(self.b.load(target), target, depth, files + (target,))

    def generation(self, cfg, where: str) -> None:
        if cfg is None:
            return
        if not isinstance(cfg, dict):
            self.err("GENERATION_CONFIG_INVALID", where, "generate_content_config must be a mapping")
            return
        bad = lambda k, msg: self.err("GENERATION_VALUE_INVALID", where, f"{k}={cfg.get(k)!r}: {msg}")
        for k in FORBIDDEN_GEN_FIELDS:
            if k in cfg:
                self.err("GENERATION_FIELD_FORBIDDEN", where, f"generate_content_config.{k} is excluded by the schema (README)")
        for k in sorted(set(cfg) - GEN_FIELDS - set(FORBIDDEN_GEN_FIELDS), key=str):
            self.r.warnings.append(Issue("GENERATION_FIELD_UNDOCUMENTED", where, f"generate_content_config.{k} not in the documented field list"))
        if "temperature" in cfg and not (_is_num(cfg["temperature"]) and cfg["temperature"] >= 0):
            bad("temperature", "must be a number >= 0")
        if "top_p" in cfg and not (_is_num(cfg["top_p"]) and 0 <= cfg["top_p"] <= 1):
            bad("top_p", "must be a number in [0, 1]")
        if "top_k" in cfg and not (_is_num(cfg["top_k"]) and cfg["top_k"] >= 1):
            bad("top_k", "must be a number >= 1")
        if "max_output_tokens" in cfg and not (_is_int(cfg["max_output_tokens"]) and MAX_OUTPUT_TOKENS[0] <= cfg["max_output_tokens"] <= MAX_OUTPUT_TOKENS[1]):
            bad("max_output_tokens", f"must be an int in {MAX_OUTPUT_TOKENS[0]}..{MAX_OUTPUT_TOKENS[1]}")
        for k in ("presence_penalty", "frequency_penalty"):
            if k in cfg and not _is_num(cfg[k]):
                bad(k, "must be a number")
        if "seed" in cfg and not _is_int(cfg["seed"]):
            bad("seed", "must be an int")
        if "stop_sequences" in cfg and not (isinstance(cfg["stop_sequences"], list) and all(isinstance(x, str) for x in cfg["stop_sequences"])):
            bad("stop_sequences", "must be a list of strings")
        if "response_mime_type" in cfg and not isinstance(cfg["response_mime_type"], str):
            bad("response_mime_type", "must be a string")
        tc = cfg.get("thinking_config")
        if "thinking_config" not in cfg:
            return
        self.r.certification_issues.append(Issue("H02_H03_THINKING_CONFIG_UNCERTIFIED", where,
                                                 "thinking_config semantics/forwarding are NOT-REPRODUCED", "H02"))
        if not isinstance(tc, dict):
            bad("thinking_config", "must be a mapping")
            return
        for k in sorted(set(tc) - THINKING_FIELDS, key=str):
            self.r.warnings.append(Issue("GENERATION_FIELD_UNDOCUMENTED", where, f"thinking_config.{k} not documented"))
        if "thinking_budget" in tc and not (_is_int(tc["thinking_budget"]) and THINKING_BUDGET[0] <= tc["thinking_budget"] <= THINKING_BUDGET[1]):
            self.err("GENERATION_VALUE_INVALID", where, f"thinking_budget={tc['thinking_budget']!r}: must be an int in {THINKING_BUDGET[0]}..{THINKING_BUDGET[1]}")
        if "include_thoughts" in tc and not isinstance(tc["include_thoughts"], bool):
            self.err("GENERATION_VALUE_INVALID", where, f"include_thoughts={tc['include_thoughts']!r}: must be a boolean")
        if "thinking_level" in tc and not (isinstance(tc["thinking_level"], str) and tc["thinking_level"].upper() in THINKING_LEVELS):
            self.err("GENERATION_VALUE_INVALID", where, f"thinking_level={tc['thinking_level']!r}: must be one of {THINKING_LEVELS} (case-insensitive)")


# ---- file inventory --------------------------------------------------------------------------

def _classify_tree(root: Path, r: Report) -> tuple[set[str], dict[str, int]]:
    """Walk without following symlinks. Returns (candidate files, sizes). Junk is excluded with a warning."""
    files, sizes = set(), {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        rel_d = d.relative_to(root).as_posix()
        rel_d = "" if rel_d == "." else rel_d
        keep = []
        for name in sorted(dirnames):
            rel = posixpath.join(rel_d, name)
            if (d / name).is_symlink():
                r.errors.append(Issue("SYMLINK", rel, "symlinked directory (symlinks are never allowed)"))
            elif name in SECRET_DIRS:
                r.errors.append(Issue("SECRET_FILE", rel, "secrets directory inside bundle"))
            elif name in DATASET_NAMES:
                r.errors.append(Issue("DATASET_ARTIFACT", rel, "raw competition dataset directory inside bundle"))
            elif name in JUNK_DIRS:
                r.warnings.append(Issue("JUNK_EXCLUDED", rel, "generated/cache directory excluded from packaging"))
                r.excluded_files.append(rel + "/")
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = posixpath.join(rel_d, name)
            st = (d / name).lstat()
            if stat.S_ISLNK(st.st_mode):
                r.errors.append(Issue("SYMLINK", rel, "symlinked file (symlinks are never allowed)"))
            elif not stat.S_ISREG(st.st_mode):
                r.errors.append(Issue("SPECIAL_FILE", rel, "not a regular file"))
            elif SECRET_RE.match(name):
                r.errors.append(Issue("SECRET_FILE", rel, "secret/credential-like file inside bundle"))
            elif name in DATASET_NAMES or name.endswith(DATASET_SUFFIXES):
                r.errors.append(Issue("DATASET_ARTIFACT", rel, "dataset/wheel/archive artifact inside bundle"))
            elif name in JUNK_NAMES or name.endswith(JUNK_SUFFIXES):
                r.warnings.append(Issue("JUNK_EXCLUDED", rel, "OS/editor/cache file excluded from packaging"))
                r.excluded_files.append(rel)
            elif any(ord(c) < 32 for c in rel) or "\\" in rel:
                r.errors.append(Issue("INVALID_PATH_NAME", rel, "control character or backslash in path"))
            else:
                files.add(rel)
                sizes[rel] = st.st_size
    return files, sizes


def validate(source: Path) -> Report:
    """Never raises on bundle content: unexpected failures become INPUT_UNPROCESSABLE (no content echoed)."""
    try:
        return _validate(Path(source))
    except RecursionError:
        r = Report()
        r.errors.append(Issue("INPUT_UNPROCESSABLE", "", "recursion limit hit while processing bundle structure"))
        return r
    except Exception as e:  # noqa: BLE001 - competitor-controlled input must never produce a traceback
        r = Report()
        r.errors.append(Issue("INPUT_UNPROCESSABLE", "", f"validator could not process bundle input ({type(e).__name__})"))
        return r


def _validate(source: Path) -> Report:
    r = Report()
    if source.is_symlink():
        r.errors.append(Issue("SYMLINK", "", "bundle root is a symlink"))
        return r
    if not source.is_dir():
        r.errors.append(Issue("SOURCE_NOT_DIR", "", "source does not exist or is not a directory"))
        return r
    files, sizes = _classify_tree(source, r)

    roots = [n for n in ROOT_CONFIG_NAMES if n in files]
    if not roots:
        r.errors.append(Issue("ROOT_CONFIG_MISSING", "", f"need exactly one of {ROOT_CONFIG_NAMES}"))
    elif len(roots) > 1:
        r.errors.append(Issue("ROOT_CONFIG_MULTIPLE", "", f"found {roots}; exactly one root config allowed"))
    r.root_config = roots[0] if len(roots) == 1 else None

    b = Bundle(source, r, files, sizes)
    adapters_ok = set()
    for rel in files:
        parts = rel.split("/")
        if parts[0] == "adapters" and len(parts) == 2 and rel.endswith(".safetensors"):
            adapters_ok.add(parts[1].removesuffix(".safetensors"))
        if parts[0] == "adapters" and len(parts) == 3 and parts[2] == "adapter_model.safetensors" \
                and f"adapters/{parts[1]}/adapter_config.json" in files:
            adapters_ok.add(parts[1])
    walk = _Walk(b, adapters_ok)
    if r.root_config:
        b.referenced.add(r.root_config)
        root_cfg = b.load(r.root_config)
        if root_cfg is not None or not r.errors:
            walk.agent(root_cfg, r.root_config, 0, (r.root_config,))
    if walk.instruction_total > MAX_TOTAL_INSTRUCTION_CHARS:
        r.errors.append(Issue("INSTRUCTION_TOTAL_TOO_LONG", "", f"{walk.instruction_total} instruction chars across agents > {MAX_TOTAL_INSTRUCTION_CHARS}"))
    if walk.skills > MAX_SKILLS:
        r.errors.append(Issue("TOO_MANY_SKILLS", "", f"{walk.skills} skills > {MAX_SKILLS}"))

    contract = {f for f in files if f in roots or f in ROOT_PACKAGED_FILES or f.split("/")[0] in PACKAGED_DIRS}
    # Every contract YAML is parsed (syntax, includes) and standalone `model:` keys count toward the
    # single-model rule (README §3.2). Must run before the packaging set is final: it can add references.
    for f in sorted(contract | set(b.referenced)):
        if f.endswith((".yaml", ".yml")):
            data = b.load(f)
            if isinstance(data, dict) and isinstance(data.get("model"), str):
                r.models.append(normalize_model(data["model"]))
    packaged = contract | b.referenced
    for f in sorted(files - packaged):
        r.warnings.append(Issue("NOT_PACKAGED", f, "not part of the submission contract and not referenced; excluded"))
        r.excluded_files.append(f)
    r.packaged_files = sorted(packaged)
    r.unpacked_bytes = sum(sizes[f] for f in packaged)

    yaml_files = [f for f in packaged if f.endswith((".yaml", ".yml"))]
    for f in sorted(packaged):
        top, ext = f.split("/")[0], posixpath.splitext(f)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            r.errors.append(Issue("UNSUPPORTED_EXTENSION", f, f"extension {ext or '(none)'} not in {ALLOWED_EXTENSIONS}"))
        elif ext == ".py" and top != "skills":
            r.errors.append(Issue("PYTHON_OUTSIDE_SKILLS", f, ".py is only allowed for skill scripts under skills/ (repo policy)"))
        elif ext == ".safetensors" and top != "adapters":
            r.errors.append(Issue("SAFETENSORS_OUTSIDE_ADAPTERS", f, ".safetensors only allowed under adapters/ (repo policy)"))
        elif top == "adapters" and ext not in (".json", ".safetensors"):
            r.errors.append(Issue("ADAPTER_UNEXPECTED_FILE", f, "adapters/ may contain only .json and .safetensors"))
        if ext in (".yaml", ".yml") and sizes[f] > MAX_YAML_BYTES:
            r.errors.append(Issue("YAML_TOO_LARGE", f, f"{sizes[f]} bytes > {MAX_YAML_BYTES}"))
        if ext in (".yaml", ".yml", ".md", ".txt", ".json", ".py") and PLACEHOLDER_MARKER.encode() in (source / f).read_bytes():
            r.errors.append(Issue("PLACEHOLDER_PRESENT", f, f"contains {PLACEHOLDER_MARKER}; template values must be replaced"))
        if ext == ".safetensors":
            problems = safetensors_check.check(source / f)
            if problems:
                r.errors.append(Issue("SAFETENSORS_CONTAINER_INVALID", f, "; ".join(problems[:5])))
    for name in sorted({p.split("/")[1].removesuffix(".safetensors") for p in packaged if p.startswith("adapters/")}):
        r.adapters_present.append(name)
        d = f"adapters/{name}"
        if f"{d}/adapter_config.json" in files:
            try:
                if not isinstance(json.loads((source / d / "adapter_config.json").read_text(encoding="utf-8")), dict):
                    raise ValueError("not an object")
            except (ValueError, UnicodeDecodeError):
                r.errors.append(Issue("ADAPTER_CONFIG_INVALID", f"{d}/adapter_config.json", "not a valid JSON object"))
        if name not in adapters_ok:
            r.errors.append(Issue("ADAPTER_INCOMPLETE", d, "needs adapter_config.json + adapter_model.safetensors"))
        if name not in r.adapters_referenced:
            r.warnings.append(Issue("ADAPTER_UNREFERENCED", d, "present but no agent references it (it may still be loaded by the server)"))
    if r.adapters_present or r.adapters_referenced:
        r.certification_issues.append(Issue("LORA_DEFERRED", "adapters", "SAFETENSORS_CONTAINER_VALID at most; LoRA runtime/model compatibility deferred (H16/H24/H25)", "H16"))

    distinct = sorted(set(r.models))
    for m in distinct:
        if m != ALLOWED_MODEL:
            r.errors.append(Issue("MODEL_NOT_ALLOWED", "", f"model {m!r} is not {ALLOWED_MODEL!r}"))
    if len(distinct) > 1:
        r.errors.append(Issue("MULTIPLE_BASE_MODELS", "", f"{len(distinct)} distinct base models declared: {distinct}"))
    if len(packaged) > MAX_FILES:
        r.errors.append(Issue("TOO_MANY_FILES", "", f"{len(packaged)} > {MAX_FILES}"))
    if len(yaml_files) > MAX_YAML_FILES:
        r.errors.append(Issue("TOO_MANY_YAML_FILES", "", f"{len(yaml_files)} > {MAX_YAML_FILES}"))
    if r.unpacked_bytes >= MAX_TOTAL_BYTES:
        r.errors.append(Issue("SIZE_LIMIT", "", f"unpacked {r.unpacked_bytes} bytes; must be < {MAX_TOTAL_BYTES}"))
    r.certification_issues.append(Issue("H23_HARNESS_VERSION_UNKNOWN", "", "validated against README documentation only; harness source/version not captured", "H23"))
    return r


def render(d: dict) -> str:
    lines = [f"{d['structural']} · {d['harness_compatibility']}",
             f"root_config={d['root_config']} packaged={len(d['packaged_files'])} unpacked_bytes={d['unpacked_bytes']} "
             f"agents={d['agent_count']} models={d['models']}"]
    for kind in ("errors", "warnings", "certification_issues"):
        lines.append(f"{kind} ({len(d[kind])}):")
        lines += [f"  [{i['code']}]{' ' + i['h_id'] if i['h_id'] else ''} {i['path'] or '.'}: {i['message']}" for i in d[kind]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--json", action="store_true", help="print the machine-readable report to stdout")
    args = ap.parse_args(argv)
    d = validate(args.source).to_dict()
    print(json.dumps(d, indent=2, sort_keys=True) if args.json else render(d))
    return 0 if d["structural"] == STRUCTURAL_VALID else 1


if __name__ == "__main__":
    sys.exit(main())
