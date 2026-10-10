"""scripts/make-cert.sh issues a certificate the iPhone will accept (#71).

The phone never says *why* it distrusts a certificate -- Safari shows the same
warning for a missing SAN, an over-long validity and a bad chain -- so every
rule iOS applies is asserted here, where a failure can name itself. The rules
are Apple's for any TLS server certificate since iOS 13: the name in the SAN,
serverAuth in the EKU, SHA-2, and at most 825 days (support.apple.com/103769).
Measured on macOS 26 with `security verify-cert` during #71: 825 days verifies
against a root made by this script and 826 is refused.

And the root's name constraint is asserted to *bite*, not merely to be present:
a leaf for another name, signed by the same root, must fail to verify. That is
the property that keeps the root's private key from being a key to every site
the phone visits.

Everything goes through the `openssl` command line, because that is all the
script uses and the suite has no X.509 library. The script runs under macOS's
LibreSSL on a Mac and OpenSSL 3 in CI, and the assertions hold for both.
"""

import os
import re
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

#: tests/deploy/test_make_cert.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "make-cert.sh"

FILES = ("darts-root.crt", "darts-root.key", "darts-leaf.crt", "darts-leaf.key")


def test_openssl_is_here() -> None:
    """A loud failure rather than a skip: without it, nothing below means anything."""
    assert shutil.which("openssl"), "openssl is required to run make-cert.sh"


def make_cert(directory: Path, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "MAKE_CERT_DIR": str(directory), **env}
    return subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, env=environment, timeout=60, check=False
    )


def openssl(*args: str | Path) -> str:
    return subprocess.run(
        ["openssl", *map(str, args)], capture_output=True, text=True, check=True
    ).stdout


def text(cert: Path) -> str:
    return openssl("x509", "-noout", "-text", "-in", cert)


def date(cert: Path, which: str) -> datetime:
    """`notAfter=Jan  7 16:35:27 2029 GMT`, from either LibreSSL or OpenSSL."""
    line = openssl("x509", "-noout", f"-{which}", "-in", cert).strip()
    value = " ".join(line.split("=", 1)[1].split())
    return datetime.strptime(value, "%b %d %H:%M:%S %Y GMT")


@pytest.fixture(scope="module")
def issued(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # A space in the path, because the real default is ~/Library/Application Support.
    directory = tmp_path_factory.mktemp("tls") / "Application Support" / "darts-tls"
    result = make_cert(directory)
    assert result.returncode == 0, result.stderr
    return directory


def test_it_writes_the_four_files(issued: Path) -> None:
    assert sorted(p.name for p in issued.iterdir()) == sorted(FILES)


def test_the_keys_and_their_directory_are_private(issued: Path) -> None:
    """The root key is the most sensitive file anywhere in this project."""
    assert stat.S_IMODE(issued.stat().st_mode) == 0o700
    for key in ("darts-root.key", "darts-leaf.key"):
        assert stat.S_IMODE((issued / key).stat().st_mode) == 0o600, key


def test_the_leaf_chains_to_the_root(issued: Path) -> None:
    out = openssl("verify", "-CAfile", issued / "darts-root.crt", issued / "darts-leaf.crt")
    assert out.strip().endswith("OK")


def test_the_leaf_meets_ios_rules(issued: Path) -> None:
    leaf = text(issued / "darts-leaf.crt")
    assert "DNS:darts.local" in leaf, "iOS reads the name from the SAN, never the CN"
    assert "TLS Web Server Authentication" in leaf, "iOS requires serverAuth in the EKU"
    assert "sha256" in leaf.lower()
    assert "CA:FALSE" in leaf


def test_the_leaf_is_valid_for_at_most_825_days(issued: Path) -> None:
    leaf = issued / "darts-leaf.crt"
    days = (date(leaf, "enddate") - date(leaf, "startdate")).days
    assert days <= 825
    assert days >= 800, "renewing every couple of years is the point of a long leaf"


def test_the_root_may_sign_leaves_only_for_darts_local(issued: Path) -> None:
    root = text(issued / "darts-root.crt")
    assert re.search(r"Basic Constraints: critical\s+CA:TRUE, pathlen:0", root)
    assert re.search(r"Name Constraints: critical", root)
    permitted = root.split("Permitted:", 1)[1].split("Excluded:", 1)[0]
    assert permitted.split() == ["DNS:darts.local"]
    excluded = root.split("Excluded:", 1)[1]
    assert "IP:0.0.0.0/0.0.0.0" in excluded, "an IPv4 SAN would escape a DNS-only constraint"
    assert "IP:0:0:0:0:0:0:0:0/0:0:0:0:0:0:0:0" in excluded, "and so would an IPv6 one"


def test_the_name_constraint_refuses_any_other_name(issued: Path, tmp_path: Path) -> None:
    """Present is not enough: a leaf for another name from this root must not verify."""
    key, csr, cert = tmp_path / "x.key", tmp_path / "x.csr", tmp_path / "x.crt"
    ext = tmp_path / "x.ext"
    ext.write_text("subjectAltName=DNS:bank.example\nextendedKeyUsage=serverAuth\n")
    openssl("ecparam", "-genkey", "-name", "prime256v1", "-noout", "-out", key)
    openssl("req", "-new", "-key", key, "-subj", "/CN=bank.example", "-out", csr)
    openssl(
        "x509", "-req", "-sha256", "-days", "30", "-set_serial", "7",
        "-in", csr, "-CA", issued / "darts-root.crt", "-CAkey", issued / "darts-root.key",
        "-extfile", ext, "-out", cert,
    )  # fmt: skip
    result = subprocess.run(
        ["openssl", "verify", "-CAfile", str(issued / "darts-root.crt"), str(cert)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "subtree violation" in result.stdout + result.stderr


def test_a_second_run_keeps_the_root_and_renews_the_leaf(tmp_path: Path) -> None:
    """The renewal path: the phone trusts the root, so the root must not change."""
    directory = tmp_path / "darts-tls"
    assert make_cert(directory).returncode == 0
    root = (directory / "darts-root.crt").read_bytes()
    root_key = (directory / "darts-root.key").read_bytes()
    leaf = (directory / "darts-leaf.crt").read_bytes()

    again = make_cert(directory)
    assert again.returncode == 0, again.stderr
    assert "reusing the root" in again.stderr
    assert (directory / "darts-root.crt").read_bytes() == root
    assert (directory / "darts-root.key").read_bytes() == root_key
    assert (directory / "darts-leaf.crt").read_bytes() != leaf
    openssl("verify", "-CAfile", directory / "darts-root.crt", directory / "darts-leaf.crt")


def test_a_leaf_longer_than_ios_allows_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "darts-tls"
    result = make_cert(directory, MAKE_CERT_LEAF_DAYS="826")
    assert result.returncode != 0
    assert "825" in result.stderr
    assert not directory.exists()


def test_half_a_root_is_refused_rather_than_replaced(tmp_path: Path) -> None:
    """A lone key or certificate means something went wrong; guessing would lose the root."""
    directory = tmp_path / "darts-tls"
    directory.mkdir()
    (directory / "darts-root.key").write_text("not really\n")
    result = make_cert(directory)
    assert result.returncode != 0
    assert "half a root" in result.stderr
    assert sorted(p.name for p in directory.iterdir()) == ["darts-root.key"]


def test_it_prints_the_steps_to_the_pi(issued: Path) -> None:
    result = make_cert(issued)
    assert "--tls-from" in result.stderr
    assert "darts-root.crt" in result.stderr


def test_help_is_not_an_error() -> None:
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "MAKE_CERT_DIR" in result.stdout
