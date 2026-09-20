"""Code lists fetched from the SDMX Global Registry, not written here.

The lists this tool builds from its own output cannot fail: a code is allowed because we
just declared it. Comparing against a list nobody here maintains is the only check that
can say no, and it is what a receiving registry would do.

Fetched once and kept on disk, because the network is not always there and a run must not
depend on it. Without a cache and without a network the caller gets None and says so
rather than pretending the check passed.
"""

import logging
from pathlib import Path

from pdf2sdmx.config import settings

log = logging.getLogger(__name__)

BASE = "https://registry.sdmx.org/sdmx/v2/structure/codelist"
AGENCY = "SDMX"
# The cross-domain lists this tool uses. Each is maintained by the SDMX Technical Working
# Group, which is the point: their content is not ours to decide.
WANTED = ("CL_FREQ", "CL_OBS_STATUS", "CL_UNIT_MULT", "CL_CONF_STATUS")


def codelist(codelist_id: str, *, refresh: bool = False) -> dict[str, str] | None:
    """Codes and names of an official list, or None when it is neither cached nor reachable."""
    path = cache_path(codelist_id)
    if refresh or not path.exists():
        downloaded = _download(codelist_id, path)
        if not downloaded and not path.exists():
            return None
    return _read(path)


def cache_path(codelist_id: str) -> Path:
    return settings.reference_dir / f"{codelist_id}.xml"


def unknown_codes(used: set[str], codelist_id: str) -> set[str] | None:
    """Codes we emit that the official list does not contain. None when it is unavailable.

    An empty set is the answer that means something: every value we wrote exists in a list
    maintained elsewhere.
    """
    official = codelist(codelist_id)
    if official is None:
        return None
    return {code for code in used if code not in official}


def download_all(refresh: bool = False) -> dict[str, int]:
    """Fetch every list this tool uses. Returns how many codes each one holds."""
    sizes = {}
    for codelist_id in WANTED:
        codes = codelist(codelist_id, refresh=refresh)
        sizes[codelist_id] = len(codes) if codes else 0
    return sizes


def _download(codelist_id: str, path: Path) -> bool:
    import requests

    url = f"{BASE}/{AGENCY}/{codelist_id}/+/?format=sdmx-2.1"
    try:
        response = requests.get(url, timeout=settings.request_timeout)
        response.raise_for_status()
    except Exception as exc:  # no network, a proxy, a registry outage: none of them fatal
        log.warning("could not fetch %s from the registry: %s", codelist_id, exc)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return True


def _read(path: Path) -> dict[str, str] | None:
    import sdmx

    try:
        message = sdmx.read_sdmx(path)
    except Exception as exc:
        log.warning("cached %s does not parse: %s", path.name, exc)
        return None
    codes: dict[str, str] = {}
    for found in message.codelist.values():
        for code in found:
            codes[code.id] = str(code.name)
    return codes or None
