"""Versioned scientific Skills bound to an allowlist of implementations.

A descriptor states what a Skill does, which arguments the planner may pass,
and what evidence and policy limits apply. The controller executes a Skill
only when its descriptor validates and names an implementation the caller
explicitly supplied. Descriptors may narrow an implementation's allowed
argument values but never widen them. Descriptor files are data; they cannot
add code, endpoints or permissions.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .graph import digest


SCHEMA_VERSION = "0.2.0"
# An installed package carries the descriptors in skills_live; a source checkout keeps them in skills/live.
_PACKAGED = Path(__file__).resolve().parent / "skills_live"
LIVE_DIRECTORY = _PACKAGED if _PACKAGED.is_dir() else Path(__file__).resolve().parent.parent / "skills" / "live"
_ID = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+\Z", re.ASCII)
_OPERATION = re.compile(r"[a-z][a-z_]{1,39}\Z", re.ASCII)
_VERSION = re.compile(r"(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\Z", re.ASCII)
_RESERVED = {"finish"}
_REQUIRED = {"$schema", "schema_version", "skill_id", "version", "name", "description", "operation",
             "implementation", "planner_template", "planner_guidance", "arguments", "sources",
             "external_actions", "evidence", "policy_constraints", "validation"}
_ARGUMENT = {"name", "type", "description"}
_EVIDENCE = {"method", "supports_causality", "limitations"}
_POLICY = {"egress", "may_grant_access", "may_sign_agreements", "attribution"}
_EGRESS = {"public_metadata_only", "local_draft_only"}


class SkillError(ValueError):
    """A descriptor or binding failed validation."""


@dataclass(frozen=True)
class Implementation:
    """Code that executes a Skill operation inside the bounded controller.

    `arguments` names every argument the code accepts. `allowed` maps
    enumerated arguments to the hard upper bound of permitted values.
    `check(action, records, skill)` raises on an invalid proposal.
    `execute(run, action, node)` performs the operation and returns an observation.
    """

    name: str
    arguments: tuple
    allowed: dict
    check: object
    execute: object


@dataclass(frozen=True)
class Skill:
    descriptor: dict
    implementation: Implementation
    sha256: str

    @property
    def operation(self) -> str:
        return self.descriptor["operation"]

    @property
    def arguments(self) -> dict:
        return {item["name"]: item for item in self.descriptor["arguments"]}

    def allowed_values(self, name: str) -> frozenset:
        """Return descriptor enum values intersected with the implementation bound."""
        bound = frozenset(self.implementation.allowed.get(name, ()))
        values = self.arguments[name].get("enum")
        return bound if values is None else bound & frozenset(values)

    def entry(self) -> dict:
        return {"skill_id": self.descriptor["skill_id"], "version": self.descriptor["version"],
                "operation": self.operation, "descriptor_sha256": self.sha256}


def _text(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SkillError(f"Skill descriptor has invalid {name}")
    return value


def _texts(value, name, allow_empty=False):
    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > 20:
        raise SkillError(f"Skill descriptor has invalid {name}")
    for item in value:
        _text(item, name, 600)
    return value


def validate_descriptor(descriptor: dict) -> dict:
    """Check a descriptor's shape with the standard library only."""
    if not isinstance(descriptor, dict) or set(descriptor) != _REQUIRED:
        raise SkillError("Skill descriptor fields differ from schema 0.2.0")
    if (descriptor["$schema"] != "../../schemas/executable_skill.schema.json"
            or descriptor["schema_version"] != SCHEMA_VERSION):
        raise SkillError("Unsupported Skill descriptor schema")
    if not isinstance(descriptor["skill_id"], str) or not _ID.fullmatch(descriptor["skill_id"]):
        raise SkillError("Invalid skill_id")
    if not isinstance(descriptor["version"], str) or not _VERSION.fullmatch(descriptor["version"]):
        raise SkillError("Skill version must be MAJOR.MINOR.PATCH")
    operation = descriptor["operation"]
    if not isinstance(operation, str) or not _OPERATION.fullmatch(operation) or operation in _RESERVED:
        raise SkillError("Invalid or reserved operation name")
    for field in ("name", "description", "implementation"):
        _text(descriptor[field], field)
    _texts(descriptor["planner_guidance"], "planner_guidance", allow_empty=True)
    _texts(descriptor["sources"], "sources")
    if descriptor["external_actions"] not in (True, False):
        raise SkillError("external_actions must be boolean")
    arguments = descriptor["arguments"]
    if not isinstance(arguments, list) or len(arguments) > 10:
        raise SkillError("Invalid arguments")
    names = []
    for item in arguments:
        if not isinstance(item, dict) or not _ARGUMENT <= set(item) or set(item) - _ARGUMENT - {"enum", "max_length"}:
            raise SkillError("Invalid argument fields")
        _text(item["name"], "argument name", 40)
        _text(item["description"], "argument description", 600)
        if item["name"] in ("action", "reason") or item["type"] not in ("string", "record_id"):
            raise SkillError("Reserved argument name or unsupported argument type")
        if "enum" in item:
            _texts(item["enum"], "argument enum")
            if len(set(item["enum"])) != len(item["enum"]):
                raise SkillError("Duplicate argument enum value")
        if "max_length" in item and (type(item["max_length"]) is not int or not 1 <= item["max_length"] <= 4000):
            raise SkillError("Invalid argument max_length")
        names.append(item["name"])
    if len(names) != len(set(names)):
        raise SkillError("Duplicate argument name")
    try:
        template = json.loads(_text(descriptor["planner_template"], "planner_template", 600))
    except json.JSONDecodeError:
        raise SkillError("planner_template must be a JSON object") from None
    if (not isinstance(template, dict) or template.get("action") != operation
            or set(template) != {"action", "reason", *names}):
        raise SkillError("planner_template must name the operation, its arguments and reason")
    evidence = descriptor["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE:
        raise SkillError("Invalid evidence block")
    _text(evidence["method"], "evidence method", 200)
    if evidence["supports_causality"] is not False:
        raise SkillError("A metadata Skill cannot support causal claims")
    _texts(evidence["limitations"], "evidence limitations")
    policy = descriptor["policy_constraints"]
    if not isinstance(policy, dict) or set(policy) != _POLICY:
        raise SkillError("Invalid policy_constraints block")
    if policy["may_grant_access"] is not False or policy["may_sign_agreements"] is not False:
        raise SkillError("A Skill cannot grant access or sign agreements")
    if policy["egress"] not in _EGRESS:
        raise SkillError("Unsupported egress class")
    _text(policy["attribution"], "attribution", 600)
    validation = descriptor["validation"]
    if not isinstance(validation, dict) or set(validation) != {"tests"}:
        raise SkillError("Invalid validation block")
    _texts(validation["tests"], "validation tests")
    return descriptor


class SkillRegistry:
    """An immutable set of validated Skills keyed by planner operation."""

    def __init__(self, descriptors, implementations):
        if not isinstance(implementations, dict):
            raise SkillError("Implementations must be an explicit mapping")
        skills, ids = {}, set()
        for descriptor in descriptors:
            validate_descriptor(descriptor)
            implementation = implementations.get(descriptor["implementation"])
            if not isinstance(implementation, Implementation) or implementation.name != descriptor["implementation"]:
                raise SkillError(f"No allowlisted implementation for {descriptor['skill_id']}")
            declared = {item["name"] for item in descriptor["arguments"]}
            if declared != set(implementation.arguments) or not set(implementation.allowed) <= declared:
                raise SkillError(f"Arguments of {descriptor['skill_id']} differ from its implementation")
            skill = Skill(descriptor, implementation, digest(descriptor))
            for name, item in skill.arguments.items():
                if "enum" in item and not set(item["enum"]) <= set(implementation.allowed.get(name, ())):
                    raise SkillError(f"{descriptor['skill_id']} widens the allowed values of {name}")
            if skill.operation in skills or descriptor["skill_id"] in ids:
                raise SkillError("Duplicate Skill operation or identifier")
            skills[skill.operation] = skill
            ids.add(descriptor["skill_id"])
        if not skills:
            raise SkillError("A registry needs at least one Skill")
        self._skills = skills

    @classmethod
    def from_directory(cls, directory, implementations, include=None):
        """Load every descriptor, keeping those accepted by the optional include(descriptor)."""
        paths = sorted(Path(directory).glob("*.skill.json"))
        descriptors = []
        for path in paths:
            with path.open(encoding="utf-8") as handle:
                descriptor = json.load(handle)
            if include is None or include(descriptor):
                descriptors.append(descriptor)
        return cls(descriptors, implementations)

    def __contains__(self, operation):
        return operation in self._skills

    def __getitem__(self, operation) -> Skill:
        return self._skills[operation]

    def __iter__(self):
        return iter(self._skills.values())

    def operations(self) -> list:
        return list(self._skills)

    def entries(self) -> list:
        return [skill.entry() for skill in self]
