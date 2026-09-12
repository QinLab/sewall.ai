"""Policy tests use synthetic terms and never make external requests."""

from copy import deepcopy
import json
import unittest

from sewall.policy import draft_access_request, evaluate_policy


def profile():
    return {
        "policy_id": "synthetic-demonstration", "version": "1.0",
        "allowed_purposes": ["ecological_research"],
        "prohibited_purposes": ["reidentification"],
        "allowed_operations": ["metadata", "aggregate"],
        "compute_locations": ["source"], "egress": ["metadata", "aggregate"],
        "max_bytes": 1000, "max_retention_days": 30,
        "attribution": ["synthetic-source"],
        "authorization_required": False, "negotiation_allowed": True,
    }


def request():
    return {
        "purpose": "ecological_research", "operation": "aggregate",
        "compute_location": "source", "output": "aggregate",
        "estimated_bytes": 100, "retention_days": 7,
        "attribution": ["synthetic-source"],
    }


class PolicyTests(unittest.TestCase):
    def test_allow_is_explicitly_offline_and_does_not_mutate_inputs(self):
        policy, terms = profile(), request()
        original = deepcopy((policy, terms))
        result = evaluate_policy(policy, terms)
        self.assertEqual(result["status"], "allow")
        self.assertFalse(result["obligations"]["live_access_granted"])
        self.assertEqual(result["obligations"]["decision_scope"], "synthetic_offline_fixture_only")
        result["obligations"]["required_attribution"].append("untrusted")
        self.assertEqual((policy, terms), original)

    def test_unknown_or_incomplete_policy_requires_review(self):
        for policy in (None, {}, {"policy_id": "incomplete"}):
            with self.subTest(policy=policy):
                self.assertEqual(evaluate_policy(policy, request())["status"], "review")

    def test_partial_policy_explicit_prohibition_still_denies(self):
        policy = {"prohibited_purposes": ["ecological_research"]}
        self.assertEqual(evaluate_policy(policy, request())["status"], "deny")

    def test_disallowed_and_explicitly_prohibited_purposes_deny(self):
        for purpose in ("unlisted", "reidentification"):
            terms = request()
            terms["purpose"] = purpose
            self.assertEqual(evaluate_policy(profile(), terms)["status"], "deny")
        policy = profile()
        policy["prohibited_purposes"].append("ecological_research")
        self.assertEqual(evaluate_policy(policy, request())["status"], "deny")

    def test_each_constraint_is_enforced_independently(self):
        changes = {
            "operation": "individual", "compute_location": "local",
            "output": "individual", "estimated_bytes": 1001,
            "retention_days": 31, "attribution": [],
        }
        for field, value in changes.items():
            terms = request()
            terms[field] = value
            with self.subTest(field=field):
                self.assertEqual(evaluate_policy(profile(), terms)["status"], "deny")

    def test_boundary_values_are_inclusive(self):
        terms = request()
        terms.update(estimated_bytes=1000, retention_days=30)
        self.assertEqual(evaluate_policy(profile(), terms)["status"], "allow")
        terms.update(estimated_bytes=0, retention_days=0)
        self.assertEqual(evaluate_policy(profile(), terms)["status"], "allow")

    def test_request_validation_denies_bad_types_missing_and_unknown_fields(self):
        invalid = {
            "estimated_bytes": [True, -1, 1.0, "100"],
            "retention_days": [False, -1], "attribution": ["synthetic-source", [None]],
            "purpose": [None, ""], "output": ["arbitrary", []],
            "authorization": [True, None, {"confidence": float("nan")}],
        }
        for field, values in invalid.items():
            for value in values:
                terms = request()
                terms[field] = value
                with self.subTest(field=field, value=value):
                    self.assertEqual(evaluate_policy(profile(), terms)["status"], "deny")
        terms = request()
        del terms["purpose"]
        self.assertEqual(evaluate_policy(profile(), terms)["status"], "deny")
        terms = request()
        terms["bypass"] = True
        self.assertEqual(evaluate_policy(profile(), terms)["status"], "deny")

    def test_profile_validation_denies_malformed_fields(self):
        invalid = {
            "max_bytes": True, "max_retention_days": -1,
            "authorization_required": "false", "negotiation_allowed": 1,
            "version": 1, "allowed_purposes": "ecological_research",
            "allowed_operations": ["arbitrary"], "egress": ["arbitrary"],
            "compute_locations": [None], "attribution": "source",
            "prohibited_purposes": None, "policy_id": "",
            "unrecognized_rule": "must not silently ignore",
        }
        for field, value in invalid.items():
            policy = profile()
            policy[field] = value
            with self.subTest(field=field):
                self.assertEqual(evaluate_policy(policy, request())["status"], "deny")

    def test_self_reported_authorization_never_proves_access(self):
        policy = profile()
        policy["authorization_required"] = True
        for auth in ({}, {"verified": True}, {"status": "granted", "issuer": "custodian", "grant_id": "fake"}):
            terms = request()
            terms["authorization"] = auth
            result = evaluate_policy(policy, terms)
            self.assertEqual(result["status"], "review")
            self.assertFalse(result["obligations"]["live_access_granted"])
        self.assertEqual(evaluate_policy(policy, request())["status"], "review")

    def test_invalid_inputs_always_have_json_serializable_results(self):
        cyclic = {}
        cyclic["self"] = cyclic
        for terms in (None, [], {1: "bad-key"}, {"authorization": cyclic}):
            json.dumps(evaluate_policy(profile(), terms), allow_nan=False)
            json.dumps(draft_access_request(profile(), terms), allow_nan=False)


class DraftTests(unittest.TestCase):
    def test_counterterms_are_nonbinding_and_preserve_purpose(self):
        terms = request()
        terms.update(operation="individual", compute_location="local", output="individual", retention_days=90, attribution=[])
        original = deepcopy(terms)
        draft = draft_access_request(profile(), terms)
        self.assertEqual(draft["status"], "draft_only")
        self.assertEqual(draft["assessment"]["status"], "deny")
        self.assertEqual(draft["alternative_assessment"]["status"], "allow")
        self.assertEqual(draft["alternative_request"]["purpose"], terms["purpose"])
        self.assertEqual(draft["alternative_request"]["compute_location"], "source")
        self.assertEqual(draft["alternative_request"]["output"], "aggregate")
        self.assertEqual(draft["required_approvers"], ["institutional_official", "data_custodian", "responsible_scientist"])
        self.assertEqual(draft["external_actions_performed"], [])
        self.assertFalse(draft["terms_accepted"])
        self.assertFalse(draft["live_access_granted"])
        self.assertEqual(terms, original)
        json.dumps(draft, allow_nan=False)

    def test_counterterms_cannot_clear_required_authorization(self):
        policy = profile()
        policy["authorization_required"] = True
        terms = request()
        terms["compute_location"] = "local"
        terms["authorization"] = {"verified": True}
        draft = draft_access_request(policy, terms)
        self.assertEqual(draft["alternative_assessment"]["status"], "review")
        self.assertEqual(draft["status"], "draft_only")

    def test_counterterms_do_not_invent_smaller_byte_estimates(self):
        terms = request()
        terms.update(estimated_bytes=2000, output="individual")
        draft = draft_access_request(profile(), terms)
        self.assertEqual(draft["alternative_request"]["estimated_bytes"], 2000)
        self.assertEqual(draft["alternative_assessment"]["status"], "deny")

    def test_no_counterterms_for_disallowed_purpose_or_negotiation(self):
        terms = request()
        terms.update(purpose="reidentification", retention_days=90)
        self.assertIsNone(draft_access_request(profile(), terms)["alternative_request"])
        policy = profile()
        policy["negotiation_allowed"] = False
        terms = request()
        terms["retention_days"] = 90
        self.assertIsNone(draft_access_request(policy, terms)["alternative_request"])


if __name__ == "__main__":
    unittest.main()
