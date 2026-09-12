"""Fail-closed policy checks for synthetic, offline Sewall.ai fixtures.

These profiles are demonstration inputs, not provider terms or access grants.
An ``allow`` decision permits only the fixture simulation. Real authorization
must be established by a custodian-controlled system outside this module.
"""

from copy import deepcopy
import json


_REPRESENTATIONS = {"metadata", "aggregate", "individual"}
_PROFILE_FIELDS = {
    "policy_id", "version", "allowed_purposes", "allowed_operations",
    "compute_locations", "egress", "max_bytes", "max_retention_days",
    "attribution", "authorization_required", "prohibited_purposes",
    "negotiation_allowed",
}
_REQUEST_FIELDS = {
    "purpose", "operation", "compute_location", "output", "estimated_bytes",
    "retention_days", "attribution", "authorization",
}
_REQUIRED_REQUEST_FIELDS = _REQUEST_FIELDS - {"authorization"}
_PROFILE_LISTS = {
    "allowed_purposes", "allowed_operations", "compute_locations", "egress",
    "attribution", "prohibited_purposes",
}


def _is_text(value):
    return type(value) is str and bool(value.strip())


def _is_text_list(value):
    return type(value) is list and all(_is_text(item) for item in value)


def _is_json(value):
    """Reject non-JSON values, including nonstring keys and cyclic inputs."""
    def check(item):
        if type(item) is dict:
            return all(type(key) is str and check(val) for key, val in item.items())
        if type(item) is list:
            return all(check(val) for val in item)
        return item is None or type(item) in (str, int, float, bool)

    try:
        return check(value) and bool(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        return False


def _validate_request(request):
    if type(request) is not dict:
        return ["Request must be a dictionary."]
    if any(type(key) is not str for key in request):
        return ["Request field names must be strings."]
    errors = []
    unknown = sorted(set(request) - _REQUEST_FIELDS)
    missing = sorted(_REQUIRED_REQUEST_FIELDS - set(request))
    if unknown:
        errors.append("Unknown request fields: " + ", ".join(unknown) + ".")
    if missing:
        errors.append("Missing request fields: " + ", ".join(missing) + ".")
    for field in ("purpose", "operation", "compute_location", "output"):
        if field in request and not _is_text(request[field]):
            errors.append(f"Request {field} must be a nonempty string.")
    for field in ("operation", "output"):
        if field in request and _is_text(request[field]):
            if request[field] not in _REPRESENTATIONS:
                errors.append(f"Request {field} is an unknown representation.")
    for field in ("estimated_bytes", "retention_days"):
        if field in request and (type(request[field]) is not int or request[field] < 0):
            errors.append(f"Request {field} must be a nonnegative integer, not a boolean.")
    if "attribution" in request and not _is_text_list(request["attribution"]):
        errors.append("Request attribution must be a list of nonempty strings.")
    if "authorization" in request:
        if type(request["authorization"]) is not dict or not _is_json(request["authorization"]):
            errors.append("Request authorization must be a dictionary of finite JSON values.")
    return errors


def _validate_profile(profile):
    """Return malformed-field errors separately from missing policy evidence."""
    if profile is None:
        return [], ["Policy profile is missing or unknown."]
    if type(profile) is not dict:
        return ["Policy profile must be a dictionary or None."], []
    if any(type(key) is not str for key in profile):
        return ["Policy field names must be strings."], []
    errors = []
    missing_evidence = []
    unknown = sorted(set(profile) - _PROFILE_FIELDS)
    missing = sorted(_PROFILE_FIELDS - set(profile))
    if unknown:
        errors.append("Unknown policy fields: " + ", ".join(unknown) + ".")
    if missing:
        missing_evidence.append("Missing policy fields: " + ", ".join(missing) + ".")
    for field in ("policy_id", "version"):
        if field in profile and not _is_text(profile[field]):
            errors.append(f"Policy {field} must be a nonempty string.")
    for field in sorted(_PROFILE_LISTS):
        if field in profile and not _is_text_list(profile[field]):
            errors.append(f"Policy {field} must be a list of nonempty strings.")
    for field in ("allowed_operations", "egress"):
        if field in profile and _is_text_list(profile[field]):
            if not set(profile[field]).issubset(_REPRESENTATIONS):
                errors.append(f"Policy {field} contains an unknown representation.")
    for field in ("max_bytes", "max_retention_days"):
        if field in profile and (type(profile[field]) is not int or profile[field] < 0):
            errors.append(f"Policy {field} must be a nonnegative integer, not a boolean.")
    for field in ("authorization_required", "negotiation_allowed"):
        if field in profile and type(profile[field]) is not bool:
            errors.append(f"Policy {field} must be a boolean.")
    return errors, missing_evidence


def _obligations(profile):
    obligations = {
        "decision_scope": "synthetic_offline_fixture_only",
        "live_access_granted": False,
        "audit_required": True,
        "policy_authority": "data_custodian",
    }
    if type(profile) is dict:
        names = {
            "policy_id": "policy_id", "version": "policy_version",
            "compute_locations": "compute_locations", "egress": "allowed_egress",
            "max_bytes": "max_bytes", "max_retention_days": "max_retention_days",
            "attribution": "required_attribution",
            "authorization_required": "authorization_required",
        }
        for source, destination in names.items():
            if source in profile:
                obligations[destination] = deepcopy(profile[source])
    return obligations


def evaluate_policy(profile: dict, request: dict) -> dict:
    """Evaluate exact-match fixture rules without obtaining or trusting a grant.

    Malformed input and explicit rule conflicts deny. Incomplete policy evidence
    and required authorization awaiting independent verification require review.
    Review and deny both stop execution. Requester-provided authorization is
    untrusted evidence even when it says ``verified: true``.
    """
    request_errors = _validate_request(request)
    profile_errors, missing_evidence = _validate_profile(profile)
    if request_errors or profile_errors:
        return {
            "status": "deny", "reasons": request_errors + profile_errors,
            "obligations": _obligations(None),
        }
    obligations = _obligations(profile)
    if profile is None:
        return {"status": "review", "reasons": missing_evidence, "obligations": obligations}

    conflicts = []
    if request["purpose"] in profile.get("prohibited_purposes", []):
        conflicts.append("Requested purpose is explicitly prohibited.")
    if "allowed_purposes" in profile and request["purpose"] not in profile["allowed_purposes"]:
        conflicts.append("Requested purpose is not in allowed_purposes.")
    if "allowed_operations" in profile and request["operation"] not in profile["allowed_operations"]:
        conflicts.append("Requested operation is not allowed.")
    if "compute_locations" in profile and request["compute_location"] not in profile["compute_locations"]:
        conflicts.append("Requested compute_location violates the compute locality constraint.")
    if "egress" in profile and request["output"] not in profile["egress"]:
        conflicts.append("Requested output is not permitted for egress.")
    if "max_bytes" in profile and request["estimated_bytes"] > profile["max_bytes"]:
        conflicts.append("Estimated egress bytes exceed max_bytes.")
    if "max_retention_days" in profile and request["retention_days"] > profile["max_retention_days"]:
        conflicts.append("Requested retention_days exceed max_retention_days.")
    if "attribution" in profile:
        missing = sorted(set(profile["attribution"]) - set(request["attribution"]))
        if missing:
            conflicts.append("Missing required attribution: " + ", ".join(missing) + ".")
    if conflicts:
        return {"status": "deny", "reasons": conflicts, "obligations": obligations}
    reviews = list(missing_evidence)
    if profile.get("authorization_required"):
        reviews.append(
            "Custodian authorization requires independent verification; requester-provided "
            "authorization cannot prove a grant and this offline harness cannot execute controlled access."
        )
    if reviews:
        return {"status": "review", "reasons": reviews, "obligations": obligations}
    return {
        "status": "allow",
        "reasons": ["Request satisfies the supplied synthetic policy for offline simulation."],
        "obligations": obligations,
    }


def draft_access_request(profile: dict, request: dict) -> dict:
    """Prepare a nonbinding access request and conservative possible counterterms.

    No request is sent or signed. No terms are accepted and no live access is
    enabled. Byte estimates are preserved because a revised estimate requires
    evidence from a revised plan. Human scientific approval is required for any
    proposed representation change.
    """
    assessment = evaluate_policy(profile, request)
    draft = {
        "status": "draft_only",
        "requested_terms": deepcopy(request) if _is_json(request) else None,
        "assessment": assessment,
        "conflicts": list(assessment["reasons"]) if assessment["status"] == "deny" else [],
        "reasons": list(assessment["reasons"]),
        "required_approvers": ["institutional_official", "data_custodian", "responsible_scientist"],
        "external_actions_performed": [],
        "terms_accepted": False,
        "live_access_granted": False,
        "alternative_request": None,
        "alternative_assessment": None,
        "proposed_changes": [],
        "authority_notice": "Only authorized institutional officials and the data custodian may agree to terms.",
    }
    errors, missing = _validate_profile(profile)
    if _validate_request(request) or errors or missing or profile is None:
        return draft
    if not profile["negotiation_allowed"]:
        draft["reasons"].append("The profile does not allow proposed counterterms.")
        return draft
    if request["purpose"] in profile["prohibited_purposes"] or request["purpose"] not in profile["allowed_purposes"]:
        draft["reasons"].append("The requested scientific purpose is preserved; no purpose substitution is proposed.")
        return draft

    alternative = deepcopy(request)
    changes = []
    if alternative["compute_location"] not in profile["compute_locations"] and profile["compute_locations"]:
        locations = profile["compute_locations"]
        alternative["compute_location"] = "source" if "source" in locations else locations[0]
        changes.append("Propose computation at an allowed location; availability remains to be confirmed.")
    if alternative["operation"] not in profile["allowed_operations"] and "aggregate" in profile["allowed_operations"]:
        alternative["operation"] = "aggregate"
        changes.append("Propose an aggregate operation, subject to scientific review.")
    if alternative["output"] not in profile["egress"] and "aggregate" in profile["egress"]:
        alternative["output"] = "aggregate"
        changes.append("Propose aggregate egress, subject to scientific and disclosure review.")
    if alternative["retention_days"] > profile["max_retention_days"]:
        alternative["retention_days"] = profile["max_retention_days"]
        changes.append("Propose retention within the allowed duration.")
    required_attribution = sorted(set(profile["attribution"]) - set(alternative["attribution"]))
    if required_attribution:
        alternative["attribution"].extend(required_attribution)
        changes.append("Propose including all required attribution.")
    if changes:
        draft["alternative_request"] = alternative
        draft["alternative_assessment"] = evaluate_policy(profile, alternative)
        draft["proposed_changes"] = changes
    if request["estimated_bytes"] > profile["max_bytes"]:
        draft["reasons"].append("A smaller egress estimate needs a revised plan and evidence; the estimate was preserved.")
    return draft
