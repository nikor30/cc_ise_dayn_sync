"""Unit tests for the rule engine (pure logic, temp SQLite DB)."""
from app.db import init_db
from app.models import MappingRule, SiteMapping
from app import rules

init_db()


def make_rule(**kw):
    defaults = dict(priority=10, description="t", enabled=True, match_tag="",
                    match_hostname="", match_family="", match_series="",
                    match_platform="", match_site="", device_type_ndg="",
                    location_mode="fixed", location_ndg="", derive_fallback="skip",
                    auto_create_location=False)
    defaults.update(kw)
    return MappingRule(**defaults)


DEVICE = {
    "hostname": "SW-SCH-A01", "family": "Switches and Hubs", "series": "IE 3300",
    "platform": "IE-3300-8P2S", "site": "Global/DE/Schierling/Building1",
    "tags": ["access", "prod"],
}


def test_hostname_regex_matches():
    assert rules.rule_matches(make_rule(match_hostname=r"^SW-SCH-.*"), DEVICE)
    assert not rules.rule_matches(make_rule(match_hostname=r"^SW-MUC-.*"), DEVICE)


def test_matching_is_case_insensitive():
    # a lowercase pattern must match an uppercase CC hostname (real-world case:
    # rule "ssto146*" vs hostname "SSTO146CIS.Global.web-int.net")
    dev = {**DEVICE, "hostname": "SSTO146CIS.Global.web-int.net"}
    assert rules.rule_matches(make_rule(match_hostname=r"ssto146.*"), dev)
    assert rules.rule_matches(make_rule(match_site=r"^global/de/"), DEVICE)
    assert rules.rule_matches(make_rule(match_tag="ACCESS"), DEVICE)
    assert rules.site_to_location(
        "GLOBAL/DE/SCHIERLING/B1",
        [SiteMapping(priority=10, site_pattern=r"^global/de/schierling",
                     location_ndg="Location#All Locations#DE#Schierling")]) \
        == "Location#All Locations#DE#Schierling"


def test_empty_criteria_are_wildcards():
    assert rules.rule_matches(make_rule(), DEVICE)


def test_criteria_are_anded():
    rule = make_rule(match_hostname=r"^SW-SCH-", match_family="Switches")
    assert rules.rule_matches(rule, DEVICE)
    rule = make_rule(match_hostname=r"^SW-SCH-", match_family="Routers")
    assert not rules.rule_matches(rule, DEVICE)


def test_tag_exact_and_regex():
    assert rules.rule_matches(make_rule(match_tag="access"), DEVICE)
    assert rules.rule_matches(make_rule(match_tag=r"^pr.d$"), DEVICE)
    assert not rules.rule_matches(make_rule(match_tag="lab"), DEVICE)


def test_first_match_wins_by_priority():
    r1 = make_rule(priority=20, description="second", match_hostname=".*")
    r2 = make_rule(priority=10, description="first", match_hostname=r"^SW-")
    match = rules.evaluate(DEVICE, [r2, r1])
    assert match.description == "first"


def test_disabled_rules_skipped():
    r1 = make_rule(enabled=False, match_hostname=".*", description="off")
    r2 = make_rule(match_hostname=".*", description="on")
    assert rules.evaluate(DEVICE, [r1, r2]).description == "on"


def test_no_match_returns_none():
    assert rules.evaluate(DEVICE, [make_rule(match_hostname="^nope$")]) is None


def test_merge_ndgs_keeps_other_dimensions():
    current = ["Device Type#All Device Types", "Location#All Locations",
               "IPSEC#Is IPSEC Device#No"]
    out = rules.merge_ndgs(current, "Device Type#All Device Types#Wired#Access Switchs#G1",
                           "Location#All Locations#DE#Schierling")
    assert "IPSEC#Is IPSEC Device#No" in out
    assert "Device Type#All Device Types#Wired#Access Switchs#G1" in out
    assert "Location#All Locations#DE#Schierling" in out
    assert "Device Type#All Device Types" not in out
    assert "Location#All Locations" not in out
    assert len(out) == 3


def test_merge_ndgs_none_leaves_dimension_untouched():
    current = ["Device Type#All Device Types", "Location#All Locations#DE"]
    out = rules.merge_ndgs(current, "Device Type#All Device Types#Wired", None)
    assert "Location#All Locations#DE" in out
    assert "Device Type#All Device Types#Wired" in out
    assert len(out) == 2


def test_is_default_ndgs():
    assert rules.is_default_ndgs(["Device Type#All Device Types", "Location#All Locations#DE"])
    assert rules.is_default_ndgs(["Device Type#All Device Types#Wired", "Location#All Locations"])
    assert not rules.is_default_ndgs(
        ["Device Type#All Device Types#Wired", "Location#All Locations#DE"])


def test_site_to_location_mapping():
    maps = [SiteMapping(priority=10, site_pattern=r"^Global/DE/Schierling/.*",
                        location_ndg="Location#All Locations#DE#Schierling"),
            SiteMapping(priority=20, site_pattern=r"^Global/DE/.*",
                        location_ndg="Location#All Locations#DE")]
    assert rules.site_to_location("Global/DE/Schierling/B1", maps) == \
        "Location#All Locations#DE#Schierling"
    assert rules.site_to_location("Global/DE/Munich/B2", maps) == "Location#All Locations#DE"
    assert rules.site_to_location("Global/US/NYC", maps) is None


def test_resolve_targets_fixed():
    rule = make_rule(device_type_ndg="Device Type#All Device Types#Wired",
                     location_mode="fixed", location_ndg="Location#All Locations#DE")
    t = rules.resolve_targets(rule, DEVICE, [])
    assert t["device_type"] == "Device Type#All Device Types#Wired"
    assert t["location"] == "Location#All Locations#DE"


def test_resolve_targets_derive_with_mapping():
    rule = make_rule(location_mode="derive")
    maps = [SiteMapping(priority=10, site_pattern=r"Schierling",
                        location_ndg="Location#All Locations#DE#Schierling")]
    t = rules.resolve_targets(rule, DEVICE, maps)
    assert t["location"] == "Location#All Locations#DE#Schierling"
    assert not t["create_location"]


def test_resolve_targets_derive_fallback_skip():
    rule = make_rule(location_mode="derive", derive_fallback="skip")
    t = rules.resolve_targets(rule, DEVICE, [])
    assert t["location"] is None


def test_resolve_targets_derive_fallback_default():
    rule = make_rule(location_mode="derive", derive_fallback="default",
                     location_ndg="Location#All Locations#Fallback")
    t = rules.resolve_targets(rule, DEVICE, [])
    assert t["location"] == "Location#All Locations#Fallback"


def test_resolve_targets_derive_fallback_create():
    rule = make_rule(location_mode="derive", derive_fallback="create")
    t = rules.resolve_targets(rule, DEVICE, [])
    assert t["location"] == "Location#All Locations#DE#Schierling#Building1"
    assert t["create_location"]


def test_derived_location_name_strips_global():
    assert rules.derived_location_name("Global/DE/Schierling") == \
        "Location#All Locations#DE#Schierling"
    assert rules.derived_location_name("") == ""


def test_invalid_regex_falls_back_to_exact():
    assert rules.rule_matches(make_rule(match_hostname="[invalid"), DEVICE) is False
    assert rules.rule_matches(make_rule(match_hostname="[invalid"),
                              {**DEVICE, "hostname": "[invalid"})
