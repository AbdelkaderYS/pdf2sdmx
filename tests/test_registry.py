"""Code lists that come from somewhere else are the only ones that can say no.

These tests must pass with no network, because a run must not depend on one. What they
check is the behaviour around the cache, not the registry's content.
"""

import pandas as pd
import pytest

from pdf2sdmx.core import registry, sdmx_out

pytest.importorskip("sdmx")


def test_a_missing_list_is_reported_as_unknown_not_as_passing(tmp_path, monkeypatch):
    """No cache and no network must not look like a successful check."""
    monkeypatch.setattr(registry.settings, "reference_dir", tmp_path)
    monkeypatch.setattr(registry, "_download", lambda codelist_id, path: False)
    assert registry.codelist("CL_NOT_THERE") is None
    assert registry.unknown_codes({"A"}, "CL_NOT_THERE") is None


def test_the_cache_is_used_without_touching_the_network(monkeypatch):
    def fail(codelist_id, path):
        raise AssertionError("the network was used although the list is cached")

    monkeypatch.setattr(registry, "_download", fail)
    codes = registry.codelist("CL_FREQ")
    if codes is None:
        pytest.skip("CL_FREQ not cached, run `make reference`")
    assert codes["A"] == "Annual"


def test_every_status_and_frequency_we_write_exists_in_the_official_list():
    """If one of ours is missing from theirs, we invented a code the standard does not have."""
    if registry.codelist("CL_OBS_STATUS") is None:
        pytest.skip("code lists not cached, run `make reference`")
    long = pd.DataFrame(
        [
            {"FREQ": freq, "OBS_STATUS": status}
            for freq in ("A", "Q", "D")
            for status in (sdmx_out.FIXED_CODES["CL_OBS_STATUS"])
        ]
    )
    assert sdmx_out.codes_outside_official_lists(long) == {}


def test_an_invented_code_is_caught():
    if registry.codelist("CL_FREQ") is None:
        pytest.skip("code lists not cached, run `make reference`")
    long = pd.DataFrame([{"FREQ": "EVERY_OTHER_TUESDAY", "OBS_STATUS": "A"}])
    outside = sdmx_out.codes_outside_official_lists(long)
    assert outside == {"FREQ": {"EVERY_OTHER_TUESDAY"}}


def test_the_dsd_carries_the_registry_list_rather_than_ours():
    """Four hand written codes are not a code list. The registry's list is."""
    if registry.codelist("CL_FREQ") is None:
        pytest.skip("code lists not cached, run `make reference`")
    lists = sdmx_out.codelists(pd.DataFrame([{"FREQ": "A"}]))
    assert len(lists["CL_FREQ"]) > len(sdmx_out.FIXED_CODES["CL_FREQ"])
