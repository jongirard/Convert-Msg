#!/usr/bin/env python3
"""
Batch convert Outlook .msg files using the extract-msg package.

Each .msg file produces an output folder named exactly after the original
filename (minus the .msg extension). Only .msg files are processed; all
other files in the directory are skipped.

Usage:
    python convert_msg.py /path/to/msg/folder
    python convert_msg.py /path/to/msg/folder --out /path/to/output
    python convert_msg.py /path/to/msg/folder --html
    python convert_msg.py /path/to/msg/folder --html --allow-fallback
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import extract_msg


def build_save_kwargs(**kwargs) -> dict:
    """
    Build the save_kwargs dictionary for extract_msg from user-provided options.
    Defaults: html=True, allowFallback=True, skipBodyNotFound=True.
    """
    save_kwargs = {
        "html": True,
        "allowFallback": True,
        "skipBodyNotFound": True,
    }
    if kwargs.get("html"):
        save_kwargs["html"] = True
    if kwargs.get("rtf"):
        save_kwargs["rtf"] = True
    if kwargs.get("raw"):
        save_kwargs["raw"] = True
    if kwargs.get("pdf"):
        save_kwargs["pdf"] = True
    if kwargs.get("allow_fallback"):
        save_kwargs["allowFallback"] = True
    if kwargs.get("skip_hidden"):
        save_kwargs["skipHidden"] = True
    if kwargs.get("skip_embedded"):
        save_kwargs["skipEmbedded"] = True
    if kwargs.get("extract_embedded"):
        save_kwargs["extractEmbedded"] = True
    if kwargs.get("skip_body_not_found"):
        save_kwargs["skipBodyNotFound"] = True
    if kwargs.get("save_header"):
        save_kwargs["saveHeader"] = True
    if kwargs.get("attachments_only"):
        save_kwargs["attachmentsOnly"] = True
    if kwargs.get("charset"):
        save_kwargs["charset"] = kwargs["charset"]
    if kwargs.get("use_content_id"):
        save_kwargs["useMsgFilename"] = True  # content-id based naming
    if kwargs.get("json"):
        save_kwargs["json"] = True
    if kwargs.get("skip_not_implemented"):
        save_kwargs["skipNotImplemented"] = True
    return save_kwargs


def convert_single_msg(msg_path: Path, output_dir: Path, save_kwargs: dict) -> None:
    """
    Convert a single .msg file. The output folder is placed inside output_dir,
    named after the .msg file's stem (filename without extension).

    Raises on failure (caller is responsible for catching).
    """
    folder_name = msg_path.stem
    dest = output_dir / folder_name

    msg = extract_msg.openMsg(str(msg_path))

    with tempfile.TemporaryDirectory() as tmp:
        msg.save(customPath=tmp, **save_kwargs)
        msg.close()

        # extract-msg creates exactly one subfolder inside tmp
        subfolders = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if subfolders:
            src = subfolders[0]
        else:
            # Fallback: treat tmp itself as the source
            src = Path(tmp)

        # Move to final destination with the correct name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(str(src), str(dest))


def add_conversion_args(parser: argparse.ArgumentParser) -> None:
    """Add all .msg conversion option flags to an argument parser."""
    parser.add_argument(
        "--html",
        action="store_true",
        help="Save body as HTML.",
    )
    parser.add_argument(
        "--rtf",
        action="store_true",
        help="Save body as RTF.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Save body as raw.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Save body as PDF (requires wkhtmltopdf).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Save output as JSON.",
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Fall back to a different body format if the chosen one is unavailable.",
    )
    parser.add_argument(
        "--save-header",
        action="store_true",
        help="Save the email header to a separate file.",
    )
    parser.add_argument(
        "--attachments-only",
        action="store_true",
        help="Only save attachments, not the body.",
    )
    parser.add_argument(
        "--skip-hidden",
        action="store_true",
        help="Skip hidden attachments (usually embedded in body).",
    )
    parser.add_argument(
        "--skip-embedded",
        action="store_true",
        help="Skip embedded .msg attachments.",
    )
    parser.add_argument(
        "--extract-embedded",
        action="store_true",
        help="Extract embedded .msg files as .msg instead of processing them.",
    )
    parser.add_argument(
        "--skip-body-not-found",
        action="store_true",
        help="Skip body silently if it cannot be found.",
    )
    parser.add_argument(
        "--skip-not-implemented",
        action="store_true",
        help="Skip attachments that are not implemented.",
    )
    parser.add_argument(
        "--use-content-id",
        action="store_true",
        help="Save attachments by Content ID.",
    )
    parser.add_argument(
        "--charset",
        type=str,
        default=None,
        help="Character set for HTML output (default: utf-8).",
    )


def kwargs_from_args(args: argparse.Namespace) -> dict:
    """Convert parsed argparse Namespace to kwargs dict for build_save_kwargs."""
    return {
        "html": args.html,
        "rtf": args.rtf,
        "raw": args.raw,
        "pdf": args.pdf,
        "json": args.json,
        "allow_fallback": args.allow_fallback,
        "save_header": args.save_header,
        "attachments_only": args.attachments_only,
        "skip_hidden": args.skip_hidden,
        "skip_embedded": args.skip_embedded,
        "extract_embedded": args.extract_embedded,
        "skip_body_not_found": args.skip_body_not_found,
        "skip_not_implemented": args.skip_not_implemented,
        "use_content_id": args.use_content_id,
        "charset": args.charset,
    }


def convert_msg_files(input_dir: Path, output_dir: Path, **kwargs) -> None:
    """
    Convert all .msg files in input_dir, saving each into a subfolder
    of output_dir named after the original file (without .msg extension).
    """
    msg_files = sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".msg"
    )

    if not msg_files:
        print(f"No .msg files found in: {input_dir}")
        sys.exit(1)

    print(f"Found {len(msg_files)} .msg file(s) in: {input_dir}\n")

    save_kwargs = build_save_kwargs(**kwargs)
    errors = []

    for msg_path in msg_files:
        print(f"Processing: {msg_path.name}")
        print(f"  -> {output_dir / msg_path.stem}")

        try:
            convert_single_msg(msg_path, output_dir, save_kwargs)
            print("  Done\n")
        except Exception as e:
            print(f"  Error: {e}\n")
            errors.append((msg_path.name, str(e)))

    # Summary
    succeeded = len(msg_files) - len(errors)
    print("=" * 60)
    print(f"Complete: {succeeded}/{len(msg_files)} converted successfully.")
    if errors:
        print("\nFailed files:")
        for name, err in errors:
            print(f"  - {name}: {err}")


def main():
    parser = argparse.ArgumentParser(
        description="Batch convert .msg files into named folders using extract-msg."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing .msg files to convert.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory. Defaults to the input directory.",
    )
    add_conversion_args(parser)

    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        print(f"Error: '{input_dir}' is not a directory.")
        sys.exit(1)

    output_dir = (args.out or input_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    convert_msg_files(
        input_dir=input_dir,
        output_dir=output_dir,
        **kwargs_from_args(args),
    )


if __name__ == "__main__":
    main()
