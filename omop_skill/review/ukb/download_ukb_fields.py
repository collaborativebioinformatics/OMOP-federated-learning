#!/usr/bin/env python3
"""Find and download official UKB synthetic TSVs containing selected field IDs.

The UKB site publishes whole field-group TSVs, not a per-column API. This
program reads only each remote header during discovery, then downloads the
whole matching files when --download is given. It verifies their published MD5.
"""

import argparse
import csv
import hashlib
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse


PAGE_URL = "https://biobank.ndph.ox.ac.uk/ukb/exinfo.cgi?src=UKB_Synthetic_Dataset.html"
MD5_URL = "https://biobank.ndph.ox.ac.uk/synthetic_dataset/tabular/tabular.md5"
DEFAULT_FIELDS = (31, 34, 53, 21001, 4080, 41270, 41280)
FIELD_RE = re.compile(r"^(\d+)-\d+\.\d+$")


class LinkCollector(HTMLParser):
    def __init__(self, page_url):
        super().__init__()
        self.page_url = page_url
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        url = urljoin(self.page_url, href)
        path = urlparse(url).path
        if "/synthetic_dataset/tabular/" in path and path.endswith(".tsv"):
            self.urls.append(url)


def read_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ukb-omop-pilot/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def discover(page_url, md5_url, wanted):
    collector = LinkCollector(page_url)
    collector.feed(read_url(page_url).decode("utf-8"))
    urls = sorted(set(collector.urls))
    if not urls:
        raise ValueError("No UKB synthetic tabular TSV links found on the page")

    checksums = {}
    for line in read_url(md5_url).decode("utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{32}", parts[0]):
            checksums[Path(parts[-1]).name] = parts[0].lower()

    matches = []
    found = set()
    for url in urls:
        # A TSV's header is a single line. Closing the response after readline
        # avoids transferring its hundreds of thousands of data rows here.
        request = urllib.request.Request(
            url, headers={"User-Agent": "ukb-omop-pilot/1.0", "Accept-Encoding": "identity"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            header_line = response.readline(4 * 1024 * 1024)
        if not header_line.endswith((b"\n", b"\r")):
            raise ValueError(f"Header too long or incomplete: {url}")
        header = next(csv.reader([header_line.decode("utf-8-sig")], delimiter="\t"))
        if not header or header[0].strip().lower() != "eid":
            raise ValueError(f"Unexpected first column in {url}: {header[:1]}")
        present = sorted({
            int(match.group(1))
            for name in header[1:]
            if (match := FIELD_RE.fullmatch(name.strip())) and int(match.group(1)) in wanted
        })
        if present:
            filename = Path(unquote(urlparse(url).path)).name
            if filename not in checksums:
                raise ValueError(f"No published MD5 for {filename}")
            matches.append({
                "filename": filename,
                "url": url,
                "field_ids": present,
                "md5": checksums[filename],
            })
            found.update(present)
    missing = wanted - found
    if missing:
        raise ValueError(f"Fields not found in UKB synthetic tabular files: {sorted(missing)}")
    return matches


def md5_file(path):
    digest = hashlib.md5()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(item, output_dir):
    destination = output_dir / item["filename"]
    if destination.exists() and md5_file(destination) == item["md5"]:
        print(f"Already verified: {destination}")
        return
    temporary = destination.with_name(destination.name + ".part")
    digest = hashlib.md5()
    request = urllib.request.Request(
        item["url"], headers={"User-Agent": "ukb-omop-pilot/1.0", "Accept-Encoding": "identity"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as target:
            total = response.headers.get("Content-Length")
            total_text = f" of {int(total) / (1024 ** 2):.0f} MiB" if total and total.isdigit() else ""
            print(f"Downloading {item['filename']}{total_text}...", flush=True)
            downloaded = 0
            next_report = 256 * 1024 * 1024
            for block in iter(lambda: response.read(1024 * 1024), b""):
                target.write(block)
                digest.update(block)
                downloaded += len(block)
                if downloaded >= next_report:
                    print(f"  {downloaded / (1024 ** 2):.0f} MiB received", flush=True)
                    next_report += 256 * 1024 * 1024
        if digest.hexdigest() != item["md5"]:
            raise ValueError(f"MD5 mismatch for {item['filename']}")
        temporary.replace(destination)
        print(f"Downloaded and verified: {destination}")
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/ukb_tabular"))
    parser.add_argument("--fields", type=int, nargs="+", default=DEFAULT_FIELDS)
    parser.add_argument("--download", action="store_true", help="Transfer files; without this, show the plan")
    parser.add_argument("--page-url", default=PAGE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--md5-url", default=MD5_URL, help=argparse.SUPPRESS)
    args = parser.parse_args()

    matches = discover(args.page_url, args.md5_url, set(args.fields))
    plan = {"field_ids": sorted(set(args.fields)), "files": matches}
    print(json.dumps(plan, indent=2))
    if args.download:
        args.output.mkdir(parents=True, exist_ok=True)
        for item in matches:
            download(item, args.output)
        (args.output / "download_manifest.json").write_text(json.dumps(plan, indent=2) + "\n")


if __name__ == "__main__":
    main()
