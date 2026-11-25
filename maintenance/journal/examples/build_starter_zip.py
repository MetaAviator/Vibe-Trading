"""Package the standard-library verifiers and public synthetic evidence."""

from pathlib import Path
import argparse
from zipfile import ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[3]
FILES = (
    "LICENSE",
    "NOTICE",
    "maintenance/journal/research_bundle_check.py",
    "maintenance/journal/sharadar_prices.py",
    "maintenance/journal/examples/synthetic_replay.py",
    "maintenance/journal/examples/synthetic_replay_fills.csv",
    "maintenance/journal/examples/starter-package-id.txt",
    "maintenance/journal/examples/starter_evidence/fills.csv",
    "maintenance/journal/examples/starter_evidence/journal.json",
    "maintenance/journal/examples/starter_evidence/research.zip",
)
README = """# Verify a synthetic trade report

Use Python 3.12. From this extracted folder, run on macOS or Linux:

    python3.12 -S -m maintenance.journal.examples.synthetic_replay verify maintenance/journal/examples/starter_evidence --expected-id "$(cat maintenance/journal/examples/starter-package-id.txt)"

On Windows PowerShell:

    py -3.12 -S -m maintenance.journal.examples.synthetic_replay verify maintenance/journal/examples/starter_evidence --expected-id (Get-Content maintenance/journal/examples/starter-package-id.txt)

Expect one closed trade, USD 0.50 in fees and USD 4.00 in net P&L. All data is synthetic.
The archive includes the verifier. See the Vibe-Trading Journal Lab to generate new evidence.
"""

REVIEW_SHA256 = "f3ed5c5800d9ca7aaf706326f183728aefc108787e41b327c736e833c784a4d6"
REVIEW_FILES = (
    "LICENSE",
    "NOTICE",
    "maintenance/journal/review_bundle.py",
    "maintenance/journal/review_trades.py",
    "maintenance/journal/review_receipt.py",
)
REVIEW_README = """# Review a synthetic trade bundle offline

Use Python 3.12 from this extracted folder. On macOS or Linux:

    python3.12 -S -m maintenance.journal.review_bundle verify review.zip --expected-sha256 "$(cat review-sha256.txt)" --verify-fifo --output-html receipt.html

On Windows PowerShell:

    py -3.12 -S -m maintenance.journal.review_bundle verify review.zip --expected-sha256 (Get-Content review-sha256.txt) --verify-fifo --output-html receipt.html

Open receipt.html. Expect USD 0.39 in fees, USD 1.61 in net trade cash and FIFO P&L,
and three matches. For the first match, find executions S001 and S003 in the
trades.csv inside review.zip. The evidence ZIP and all trades are synthetic.
The provided hash identifies this sample. For your own exports, record that hash
separately when creating the bundle and compare it on the receiving machine.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--review-bundle", type=Path, help="Package the canonical synthetic FIFO review ZIP")
    args = parser.parse_args()
    files, readme = FILES, README
    review = None
    if args.review_bundle:
        from maintenance.journal.review_bundle import verify_bundle

        review = args.review_bundle.read_bytes()
        verify_bundle(review, expected_sha256=REVIEW_SHA256, verify_fifo=True)
        files, readme = REVIEW_FILES, REVIEW_README
    with ZipFile(args.output, "x") as archive:
        archive.writestr(ZipInfo("README.md"), readme)
        for path in files:
            content = (ROOT / path).read_bytes()
            if not path.endswith(".zip"):
                content = content.replace(b"\r\n", b"\n")
            archive.writestr(ZipInfo(path), content)
        if review is not None:
            archive.writestr(ZipInfo("review.zip"), review)
            archive.writestr(ZipInfo("review-sha256.txt"), REVIEW_SHA256 + "\n")


if __name__ == "__main__":
    main()
