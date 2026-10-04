"""Version identifiers of the Walk Planner (algorithm, API, data bundle, interest model).

ALGORITHM_VERSION follows semver: MAJOR = API contract break, MINOR = intended change of plans
(golden scenarios regenerated in the same release), PATCH = no change of any golden output.
"""

__all__ = ["ALGORITHM_VERSION", "API_VERSION", "BUNDLE_SCHEMA_VERSION", "INTEREST_VERSION"]

ALGORITHM_VERSION = "1.0.0"
API_VERSION = "v1"
BUNDLE_SCHEMA_VERSION = 1
INTEREST_VERSION = "walk_interest_v1"
