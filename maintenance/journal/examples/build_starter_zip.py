"""Package the standard-library verifier and public synthetic replay evidence."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with ZipFile(args.output, "x") as archive:
        archive.writestr(ZipInfo("README.md"), README)
        for path in FILES:
            content = (ROOT / path).read_bytes()
            if not path.endswith(".zip"):
                content = content.replace(b"\r\n", b"\n")
            archive.writestr(ZipInfo(path), content)


if __name__ == "__main__":
    main()
