import argparse
import struct
from pathlib import Path

BLOCK = 512
ENTRY = struct.Struct("<H38sqqQ")
BOOT_OFFSET = 0xB000


class Image:
    def __init__(self, stream):
        self.stream = stream
        self.size = stream.seek(0, 2)
        boot = self.read(BOOT_OFFSET, BLOCK)
        if boot[3:8] != b"\x88\0\0\0\0" or boot[510:] != b"\x55\xaa":
            raise ValueError("Not a supported TempleOS RedSea CD image")
        self.root = struct.unpack_from("<q", boot, 24)[0]
        attr, _, cluster, self.root_size, _ = ENTRY.unpack(
            self.read(self.root * BLOCK, ENTRY.size)
        )
        if not attr & 0x10 or cluster != self.root:
            raise ValueError("Invalid RedSea root directory")

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError("File extent is outside the ISO image")
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise ValueError("Incomplete ISO image")
        return data

    def files(self, cluster=None, size=None, parent=Path(), ancestors=frozenset()):
        cluster = self.root if cluster is None else cluster
        size = self.root_size if size is None else size
        if cluster in ancestors or size % ENTRY.size:
            raise ValueError("Invalid or cyclic RedSea directory")
        data = self.read(cluster * BLOCK, size)
        names = set()
        for attr, raw_name, start, length, _ in ENTRY.iter_unpack(data):
            name = raw_name.split(b"\0", 1)[0].decode("ascii")
            if not name:
                break
            directory = bool(attr & 0x10)
            if attr & 0x100 or name in (".", "..") or (directory and start == cluster):
                continue
            stem = name.split(".")[0].upper()
            reserved = {"CON", "PRN", "AUX", "NUL"} | {
                f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
            }
            if (
                any(c in name for c in '<>:"/\\|?*')
                or any(ord(c) < 32 for c in name)
                or name.endswith((" ", "."))
                or stem in reserved
                or name.casefold() in names
            ):
                raise ValueError(f"Unsupported Windows filename: {name!r}")
            names.add(name.casefold())
            if length < 0 or start < 0 or start * BLOCK + length > self.size:
                raise ValueError(f"Invalid file extent: {name}")
            if length and not attr & 0x800:
                raise ValueError(f"Non-contiguous RedSea entry is unsupported: {name}")
            path = parent / name
            yield path, directory, start * BLOCK, length
            if directory:
                yield from self.files(start, length, path, ancestors | {cluster})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path, help="TempleOS RedSea ISO image")
    parser.add_argument(
        "output", nargs="?", type=Path, help="New or empty destination directory"
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Compatibility option; compressed files are always preserved",
    )
    args = parser.parse_args()
    destination = args.output or Path.cwd() / f"{args.iso.stem}_extracted"
    try:
        if destination.is_symlink() or (
            destination.exists()
            and (not destination.is_dir() or any(destination.iterdir()))
        ):
            raise ValueError(
                f"Destination must be a new or empty directory: {destination}"
            )
        with args.iso.open("rb") as stream:
            image = Image(stream)
            entries = list(image.files())  # Validate the tree before writing any files.
            destination.mkdir(parents=True, exist_ok=True)
            count = 0
            for path, directory, offset, size in entries:
                target = destination / path
                if directory:
                    target.mkdir()
                else:
                    with target.open("xb") as output:
                        for position in range(0, size, 1024 * 1024):
                            output.write(
                                image.read(
                                    offset + position, min(1024 * 1024, size - position)
                                )
                            )
                    count += 1
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(1, f"Extraction failed: {error}\n")
    print(f"Extracted {count} files to {destination.resolve()}")
    print("TempleOS compressed files (.Z / .C) are preserved as stored in the ISO.")


if __name__ == "__main__":
    main()
