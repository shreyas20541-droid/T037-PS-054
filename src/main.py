import argparse
from pathlib import Path

from src.processor import run_pipeline


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Process the PS-054 stored-value ledger command file.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "sample_data" / "T037-PS-054-sample-data",
        help="Directory containing stores.csv, wallets.csv, and commands.txt",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "output",
        help="Directory for the SQLite database and CSV reports",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    run_pipeline(
        str(args.output_dir / "ledger.db"),
        str(args.input_dir / "stores.csv"),
        str(args.input_dir / "wallets.csv"),
        str(args.input_dir / "commands.txt"),
        str(args.output_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())