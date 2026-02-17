"""Archive utility for Dead Drop Hub rooms.

Handles gzip archival of room SQLite DBs, index management,
TTL expiration, and restore operations.
"""

import gzip
import json
import os
import shutil
import datetime


def archive_room_db(room_name: str, db_path: str, archive_dir: str) -> str:
    """Gzip a room's SQLite DB and move it to the archive directory.

    Args:
        room_name: Name of the room (used in archive filename).
        db_path: Path to the live SQLite DB file.
        archive_dir: Directory to store archives.

    Returns:
        Path to the created archive file.

    Raises:
        FileNotFoundError: If db_path doesn't exist.
        OSError: If archive_dir can't be created.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")

    os.makedirs(archive_dir, exist_ok=True)

    date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"{room_name}_{date_str}.db.gz"
    archive_path = os.path.join(archive_dir, archive_name)

    with open(db_path, "rb") as f_in:
        with gzip.open(archive_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)

    return archive_path


def update_index(archive_dir: str, room_name: str, metadata: dict) -> None:
    """Write or update archive/index.json with room metadata.

    Each entry in the index is keyed by room_name. Metadata is merged
    with any existing entry (new keys overwrite, old keys preserved).

    Args:
        archive_dir: Directory containing index.json.
        metadata: Dict with keys like archive_path, archived_at,
                  team_names, agent_count, message_count, pinned, etc.
    """
    index_path = os.path.join(archive_dir, "index.json")

    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            index = json.load(f)
    else:
        index = {}

    # Merge metadata into existing entry
    existing = index.get(room_name, {})
    existing.update(metadata)
    existing.setdefault("archived_at", datetime.datetime.now().isoformat())
    existing.setdefault("pinned", False)
    index[room_name] = existing

    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


def cleanup_expired(archive_dir: str, ttl_days: int = 90) -> list[str]:
    """Delete archives older than TTL unless pinned.

    Args:
        archive_dir: Directory containing index.json and .db.gz files.
        ttl_days: Days after which unpinned archives are deleted.

    Returns:
        List of deleted room names.
    """
    index_path = os.path.join(archive_dir, "index.json")
    if not os.path.exists(index_path):
        return []

    with open(index_path, "r") as f:
        index = json.load(f)

    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=ttl_days)
    deleted = []

    for room_name, entry in list(index.items()):
        # Skip pinned archives
        if entry.get("pinned", False):
            continue

        archived_at = entry.get("archived_at", "")
        if not archived_at:
            continue

        try:
            archive_date = datetime.datetime.fromisoformat(archived_at)
        except (ValueError, TypeError):
            continue

        if archive_date < cutoff:
            # Delete the archive file
            archive_path = entry.get("archive_path", "")
            if archive_path and os.path.exists(archive_path):
                os.remove(archive_path)

            del index[room_name]
            deleted.append(room_name)

    # Write updated index
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    return deleted


def restore_room_db(archive_path: str, target_dir: str) -> str:
    """Gunzip an archive back to a working directory.

    Args:
        archive_path: Path to the .db.gz archive file.
        target_dir: Directory to place the restored DB.

    Returns:
        Path to the restored SQLite DB file.

    Raises:
        FileNotFoundError: If archive_path doesn't exist.
    """
    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    os.makedirs(target_dir, exist_ok=True)

    # Derive DB filename: strip .gz from the archive name
    base_name = os.path.basename(archive_path)
    if base_name.endswith(".gz"):
        db_name = base_name[:-3]  # room_20260217_120000.db
    else:
        db_name = base_name + ".db"

    db_path = os.path.join(target_dir, db_name)

    with gzip.open(archive_path, "rb") as f_in:
        with open(db_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    return db_path
