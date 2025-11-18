import os
import sys
import json
import time
import shutil
import threading
import queue
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from appdirs import user_data_dir  # NEW: use appdirs

# ============================================================
# CONSTANTS & APP-DIR SETTINGS
# ============================================================

APP_NAME = "FileTransfer"
APP_AUTHOR = "TealDowd"
STATUS_FILENAME = "transfer_status.json"
SETTINGS_FILENAME = "settings.json"


def get_app_dir() -> Path:
    """
    Return per-user app directory for this app, using appdirs.
    """
    app_dir_str = user_data_dir(APP_NAME, APP_AUTHOR)
    app_dir = Path(app_dir_str)
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


APP_DIR = get_app_dir()
TRACKING_FILE = APP_DIR / STATUS_FILENAME
SETTINGS_FILE = APP_DIR / SETTINGS_FILENAME


# ============================================================
# RULE SYSTEM
# ============================================================

class RuleEngine:
    """
    Encapsulates folder and file rules.
    Rules format:

    folder_rules: list of dicts, each like:
        {"pattern": "_ml_data_", "action": "skip"}
    file_rules: list of dicts, each like:
        {"ext": ".wav", "pattern": "mic0_data.wav", "action": "allow"}
        {"ext": ".wav", "action": "skip"}
        {"ext": ".pkl", "action": "skip"}
        {"pattern": "some_substring", "action": "skip"}  # no ext filter
    """

    def __init__(self, folder_rules=None, file_rules=None):
        self.folder_rules = folder_rules or []
        self.file_rules = file_rules or []

    @staticmethod
    def _match_rule(name: str, ext: str, rule: dict) -> bool:
        rule_ext = rule.get("ext", "*")
        rule_pat = rule.get("pattern")
        if rule_ext != "*" and rule_ext.lower() != ext.lower():
            return False
        if rule_pat is not None and rule_pat.lower() not in name.lower():
            return False
        return True

    def folder_action(self, path: Path) -> str:
        """
        Returns "allow" or "skip" (or custom), based on folder_rules.
        Default is "allow".
        """
        name = path.name
        for rule in self.folder_rules:
            pattern = rule.get("pattern")
            if pattern and pattern.lower() in name.lower():
                return rule.get("action", "allow")
        return "allow"

    def should_skip_folder(self, path: Path) -> bool:
        return self.folder_action(path) == "skip"

    def file_action(self, path: Path) -> str:
        """
        Returns an action string. Default is "allow".
        """
        name = path.name
        ext = path.suffix
        for rule in self.file_rules:
            if self._match_rule(name, ext, rule):
                return rule.get("action", "allow")
        return "allow"

    def should_copy_file(self, path: Path) -> bool:
        return self.file_action(path) == "allow"


# ============================================================
# UTILS
# ============================================================

def safeiterdir(path: Path):
    """
    List immediate subdirectories, ignoring errors.
    """
    try:
        return [d for d in path.iterdir() if d.is_dir()]
    except Exception:
        return []


def load_json(path: Path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


# ============================================================
# TRANSFER MANAGER (BACKGROUND WORKER)
# ============================================================

class TransferManager:
    """
    Handles scanning, copying, and status tracking in a worker thread.
    Communicates with the GUI via a message queue (command, payload).
    """

    def __init__(self, msg_queue: queue.Queue, rule_engine: RuleEngine):
        self.msg_queue = msg_queue
        self.rule_engine = rule_engine

        self.source_dir: Path | None = None
        self.dest_dir: Path | None = None

        # tracking: we keep file-level keys only
        self.file_done = set()
        
        # retry configuration
        self.max_retries = 3
        self.retry_delay = 1.0  # seconds between retries

        # runtime state
        self.is_stopped = False
        self.start_time: float | None = None
        self.bytes_copied = 0
        self.thread: threading.Thread | None = None

        # overwrite mode: if False, skip existing files at destination
        self.overwrite = False

        # directory-overview depth (GUI-configurable)
        self.overview_depth = 2

        self._init_status_from_file()

    # ---------- config ----------

    def set_paths(self, src: Path, dst: Path):
        self.source_dir = src
        self.dest_dir = dst

    def set_overwrite(self, overwrite: bool):
        self.overwrite = overwrite

    def set_overview_depth(self, depth: int):
        # Ensure a sane, positive depth
        self.overview_depth = max(1, int(depth))

    # ---------- status persistence ----------

    def _init_status_from_file(self):
        data = load_json(TRACKING_FILE, default={})
        self.file_done = set(data.get("file_done", []))

    def save_status(self):
        data = {
            "file_done": sorted(self.file_done),
        }
        save_json(TRACKING_FILE, data)

    # ---------- helpers ----------

    def get_speed(self) -> float:
        if not self.start_time:
            return 0.0
        elapsed = time.time() - self.start_time
        if elapsed <= 0:
            return 0.0
        return self.bytes_copied / elapsed

    def stop(self):
        self.is_stopped = True
        self.msg_queue.put(("log", "Stop requested. Flagging worker to stop."))

    # ---------- public API ----------

    def start_transfer(self):
        if not self.source_dir or not self.dest_dir:
            self.msg_queue.put(("error", "Source or destination not set."))
            return

        self.is_stopped = False
        self.start_time = time.time()
        self.bytes_copied = 0

        self.msg_queue.put(("log", f"Starting transfer. "
                            f"Source={self.source_dir}, Dest={self.dest_dir}, "
                            f"Overwrite={self.overwrite}"))
        self.msg_queue.put(("log", f"Tracking file: {TRACKING_FILE}"))
        self.msg_queue.put(
            ("log", f"Files already done: {len(self.file_done)}"))

        self.thread = threading.Thread(target=self._worker_run, daemon=True)
        self.thread.start()

    # ---------- worker main ----------

    def _worker_run(self):
        try:
            src = self.source_dir
            dst = self.dest_dir
            assert src is not None and dst is not None

            self.msg_queue.put(("log", "Worker thread started."))

            # Initial overview of source/dest (directory overview up to n levels)
            self.msg_queue.put(
                ("log", f"Building directory overview (depth={self.overview_depth}) for source and dest..."))
            self._send_directory_overview(src, dst)
            self.msg_queue.put(("log", "Overview built and sent to GUI."))

            # Collect level-1 folders
            self.msg_queue.put(
                ("log", f"Scanning level-1 folders under source: {src}"))
            level1_dirs = [d for d in safeiterdir(src)
                           if not self.rule_engine.should_skip_folder(d)]
            self.msg_queue.put(
                ("log", f"Found {len(level1_dirs)} level-1 folders."))

            # Process each level-1 folder
            for idx, l1 in enumerate(level1_dirs, start=1):
                if self.is_stopped:
                    self.msg_queue.put(
                        ("log", "Worker detected stop flag. Breaking main loop."))
                    break

                l1_key = l1.relative_to(src).as_posix()
                self.msg_queue.put(
                    ("log", f"[L1 {idx}/{len(level1_dirs)}] Considering folder: {l1_key}"))

                # Level-2 subfolders
                self.msg_queue.put(
                    ("log", f"  Scanning level-2 folders in: {l1}"))
                level2_dirs = [d for d in safeiterdir(l1)
                               if not self.rule_engine.should_skip_folder(d)]
                l2_keys = [d.relative_to(src).as_posix() for d in level2_dirs]
                self.msg_queue.put(
                    ("log", f"  Found {len(level2_dirs)} level-2 subfolders."))
                self.msg_queue.put(("l2_for_l1", {
                    "l1_key": l1_key,
                    "l2_all": l2_keys,
                }))

                # Process each level-2 folder recursively
                for jdx, sub in enumerate(level2_dirs, start=1):
                    if self.is_stopped:
                        self.msg_queue.put(
                            ("log", "  Stop flag seen while processing level-2. Breaking."))
                        break

                    rel_key = sub.relative_to(src).as_posix()
                    self.msg_queue.put(
                        ("log", f"  [L2 {jdx}/{len(level2_dirs)}] Considering folder: {rel_key}"))

                    self._process_folder_recursive(sub, rel_key)

                # Also process files directly under this level-1 folder (non-recursive)
                if not self.is_stopped:
                    self.msg_queue.put(
                        ("log", f"  Processing files directly inside L1 folder: {l1_key}"))
                    self._process_single_folder_files(l1, l1_key)

                # Mark this level-1 folder as done if we didn't stop
                if not self.is_stopped:
                    # Update destination tree for this completed folder
                    self._update_dest_tree_for_folder(l1_key)

            if self.is_stopped:
                self.msg_queue.put(("stopped", None))
            else:
                self.msg_queue.put(("all_done", None))

            self.msg_queue.put(("log", "Worker thread finished normally."))

        except Exception as e:
            self.msg_queue.put(("error", f"Fatal error in worker: {e}"))
            self.msg_queue.put(("log", f"Exception in worker: {e!r}"))

    # ---------- scanning and copying ----------

    def _process_single_folder_files(self, folder: Path, folder_key: str):
        """
        Copy files directly inside `folder` (non-recursive) following file rules.
        These files are tracked individually via `file_done`.
        """
        if self.is_stopped:
            self.msg_queue.put(
                ("log", f"  Stop flag set before processing files in folder {folder_key}."))
            return

        src_root = self.source_dir
        dst_root = self.dest_dir
        assert src_root is not None and dst_root is not None

        self.msg_queue.put(("current_folder", folder_key))

        files_to_copy: list[tuple[Path, Path, str]] = []
        skipped_by_rule = 0
        skipped_existing = 0
        skipped_already_done = 0
        
        try:
            for entry in folder.iterdir():
                if self.is_stopped:
                    self.msg_queue.put(
                        ("log", f"    Stop flag set while listing files in {folder_key}."))
                    return
                if entry.is_dir():
                    continue
                if not self.rule_engine.should_copy_file(entry):
                    skipped_by_rule += 1
                    continue

                rel_path = entry.relative_to(src_root).as_posix()

                # Already copied?
                if rel_path in self.file_done:
                    skipped_already_done += 1
                    continue

                dst_path = dst_root / entry.relative_to(src_root)

                # Overwrite logic
                if not self.overwrite and dst_path.exists():
                    skipped_existing += 1
                    self.file_done.add(rel_path)
                    continue

                files_to_copy.append((entry, dst_path, rel_path))
        except Exception as e:
            self.msg_queue.put(("error", f"Error scanning {folder}: {e}"))
            self.msg_queue.put(
                ("log", f"Error scanning folder {folder}: {e!r}"))
            return

        # Log summary instead of individual file messages
        total_skipped = skipped_by_rule + skipped_existing + skipped_already_done
        if total_skipped > 0 or len(files_to_copy) > 0:
            skip_details = []
            if skipped_by_rule > 0:
                skip_details.append(f"{skipped_by_rule} by rule")
            if skipped_existing > 0:
                skip_details.append(f"{skipped_existing} existing")
            if skipped_already_done > 0:
                skip_details.append(f"{skipped_already_done} already done")
            
            summary = f"    {folder_key}: {len(files_to_copy)} to copy"
            if total_skipped > 0:
                summary += f", {total_skipped} skipped ({', '.join(skip_details)})"
            self.msg_queue.put(("log", summary))

        self.msg_queue.put(
            ("folder_files", [p.name for p, _, _ in files_to_copy])
        )

        for src, dst, rel_key in files_to_copy:
            if self.is_stopped:
                self.msg_queue.put(
                    ("log", f"    Stop flag set while copying files in {folder_key}."))
                return
            
            # Retry logic for copying
            copied = False
            for attempt in range(1, self.max_retries + 1):
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    size = src.stat().st_size

                    if attempt == 1:
                        self.msg_queue.put(("file_start", {
                            "name": src.name,
                            "size": size,
                            "rel": rel_key,
                        }))
                        self.msg_queue.put(("log", f"      Copying file: "
                                            f"{rel_key} ({size} bytes) -> {dst}"))
                    else:
                        self.msg_queue.put(("log", f"      Retry {attempt}/{self.max_retries} copying file: "
                                            f"{rel_key} ({size} bytes) -> {dst}"))
                    
                    shutil.copy2(src, dst)

                    self.bytes_copied += size
                    self.file_done.add(rel_key)
                    self.msg_queue.put(("file_copied", rel_key))
                    # Update destination tree incrementally
                    self._add_item_to_dest_tree(rel_key)
                    self._safe_save_status()
                    copied = True
                    break
                except Exception as e:
                    if attempt < self.max_retries:
                        self.msg_queue.put(("log", f"      Copy attempt {attempt} failed: {e!r}. Retrying in {self.retry_delay}s..."))
                        time.sleep(self.retry_delay)
                    else:
                        self.msg_queue.put(("error", f"Failed to copy {src} after {self.max_retries} attempts: {e}"))
                        self.msg_queue.put(("log", f"Exception copying {src} after {self.max_retries} attempts: {e!r}"))
            
            if not copied:
                # If copy failed after all retries, stop processing this folder
                self.msg_queue.put(("error", f"Stopping processing of folder {folder_key} due to copy failure."))
                return

        self.msg_queue.put(("current_folder_done", folder_key))

    def _process_folder_recursive(self, folder: Path, folder_key: str):
        """
        Recursively copy files in `folder`, skipping folders according to rules.
        """
        if self.is_stopped:
            self.msg_queue.put(
                ("log", f"  Stop flag set before recursive processing of {folder_key}."))
            return

        src_root = self.source_dir
        dst_root = self.dest_dir
        assert src_root is not None and dst_root is not None

        self.msg_queue.put(("current_folder", folder_key))

        files_to_copy: list[tuple[Path, Path, str]] = []
        skipped_by_rule = 0
        skipped_existing = 0
        skipped_already_done = 0

        try:
            for root, dirs, files in os.walk(folder):
                if self.is_stopped:
                    self.msg_queue.put(
                        ("log", f"    Stop flag set while walking {folder_key}."))
                    return

                root_path = Path(root)

                # Filter dirs in-place based on folder rules
                original_dirs = list(dirs)
                dirs[:] = [
                    d for d in dirs
                    if not self.rule_engine.should_skip_folder(root_path / d)
                ]

                for fn in files:
                    src_path = root_path / fn
                    if not self.rule_engine.should_copy_file(src_path):
                        skipped_by_rule += 1
                        continue

                    rel_key = src_path.relative_to(src_root).as_posix()

                    if rel_key in self.file_done:
                        skipped_already_done += 1
                        continue

                    dst_path = dst_root / src_path.relative_to(src_root)

                    # Overwrite logic
                    if not self.overwrite and dst_path.exists():
                        skipped_existing += 1
                        self.file_done.add(rel_key)
                        continue

                    files_to_copy.append((src_path, dst_path, rel_key))
        except Exception as e:
            self.msg_queue.put(
                ("error", f"Error scanning folder {folder}: {e}")
            )
            self.msg_queue.put(("log", f"Exception scanning {folder}: {e!r}"))
            return

        # Log summary instead of individual file messages
        total_skipped = skipped_by_rule + skipped_existing + skipped_already_done
        if total_skipped > 0 or len(files_to_copy) > 0:
            skip_details = []
            if skipped_by_rule > 0:
                skip_details.append(f"{skipped_by_rule} by rule")
            if skipped_existing > 0:
                skip_details.append(f"{skipped_existing} existing")
            if skipped_already_done > 0:
                skip_details.append(f"{skipped_already_done} already done")
            
            summary = f"    {folder_key}: {len(files_to_copy)} to copy"
            if total_skipped > 0:
                summary += f", {total_skipped} skipped ({', '.join(skip_details)})"
            self.msg_queue.put(("log", summary))

        self.msg_queue.put(
            ("folder_files", [p.name for p, _, _ in files_to_copy])
        )

        for src, dst, rel_key in files_to_copy:
            if self.is_stopped:
                self.msg_queue.put(
                    ("log", f"    Stop flag set while copying in recursive folder {folder_key}."))
                return
            
            # Retry logic for copying
            copied = False
            for attempt in range(1, self.max_retries + 1):
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    size = src.stat().st_size

                    if attempt == 1:
                        self.msg_queue.put(("file_start", {
                            "name": src.name,
                            "size": size,
                            "rel": rel_key,
                        }))
                        self.msg_queue.put(("log", f"      Copying file: "
                                            f"{rel_key} ({size} bytes) -> {dst}"))
                    else:
                        self.msg_queue.put(("log", f"      Retry {attempt}/{self.max_retries} copying file: "
                                            f"{rel_key} ({size} bytes) -> {dst}"))
                    
                    shutil.copy2(src, dst)

                    self.bytes_copied += size
                    self.file_done.add(rel_key)
                    self.msg_queue.put(("file_copied", rel_key))
                    # Update destination tree incrementally
                    self._add_item_to_dest_tree(rel_key)
                    self._safe_save_status()
                    copied = True
                    break
                except Exception as e:
                    if attempt < self.max_retries:
                        self.msg_queue.put(("log", f"      Copy attempt {attempt} failed: {e!r}. Retrying in {self.retry_delay}s..."))
                        time.sleep(self.retry_delay)
                    else:
                        self.msg_queue.put(("error", f"Failed to copy {src} after {self.max_retries} attempts: {e}"))
                        self.msg_queue.put(("log", f"Exception copying {src} after {self.max_retries} attempts: {e!r}"))
            
            if not copied:
                # If copy failed after all retries, stop processing this folder
                self.msg_queue.put(("error", f"Stopping processing of folder {folder_key} due to copy failure."))
                return

        # Folder fully processed
        self.msg_queue.put(("folder_done", folder_key))
        self.msg_queue.put(("current_folder_done", folder_key))
        # Update destination tree for this completed folder
        self._update_dest_tree_for_folder(folder_key)

    def _safe_save_status(self):
        try:
            self.save_status()
            self.msg_queue.put(("log", "      Status saved to tracking JSON."))
        except Exception as e:
            self.msg_queue.put(("log", f"      Failed to save status: {e!r}"))

    # ---------- incremental destination tree updates ----------

    def _add_item_to_dest_tree(self, rel_path: str):
        """
        Send a message to update the destination tree incrementally when a new
        file or folder is added. This avoids re-scanning the entire destination.
        """
        # Only send updates if we're within the overview depth
        path_parts = rel_path.split('/')
        if len(path_parts) <= self.overview_depth:
            self.msg_queue.put(("dest_item_added", rel_path))

    def _update_dest_tree_for_folder(self, folder_key: str):
        """
        Update the destination tree for a completed folder by scanning
        the destination directory and sending an update message.
        """
        if not self.dest_dir:
            return
        
        # Check if folder is within overview depth
        path_parts = folder_key.split('/')
        if len(path_parts) > self.overview_depth:
            return
        
        # Build the destination path for this folder
        dst_folder_path = self.dest_dir / folder_key
        
        # Check if the folder exists in destination
        if not dst_folder_path.exists() or not dst_folder_path.is_dir():
            return
        
        # Send message to refresh destination tree for this folder
        # The GUI will scan the destination and update the tree
        self.msg_queue.put(("dest_folder_completed", folder_key))

    # ---------- overview (lightweight, directory-only) ----------

    def _send_directory_overview(self, src: Path, dst: Path):
        """
        Build a directory overview up to `self.overview_depth` levels deep for both
        source and destination, and send to GUI.

        Result format:
        {
            "root": "C:/source",
            "children": [
                {
                    "name": "sub1",
                    "rel": "sub1",
                    "children": [
                        {"name": "sub1_1", "rel": "sub1/sub1_1", "children": [...]},
                        ...
                    ]
                },
                ...
            ]
        }
        """

        max_depth = self.overview_depth

        def build_tree(root: Path, max_depth: int):
            root_str = root.as_posix()

            def walk_dir(current_path: Path, rel_base: str, depth: int):
                # Stop if we've reached the maximum depth
                if depth >= max_depth:
                    return []

                children = []
                try:
                    with os.scandir(current_path) as it:
                        for entry in it:
                            if not entry.is_dir(follow_symlinks=False):
                                continue

                            # Compute relative path from root
                            if rel_base:
                                rel = f"{rel_base}/{entry.name}"
                            else:
                                rel = entry.name

                            node = {
                                "name": entry.name,
                                "rel": rel,
                            }

                            # Recurse into subdirectories if we still have depth left
                            sub_path = current_path / entry.name
                            sub_children = walk_dir(sub_path, rel, depth + 1)
                            if sub_children:
                                node["children"] = sub_children

                            children.append(node)
                except Exception:
                    pass

                return children

            return {
                "root": root_str,
                "children": walk_dir(root, rel_base="", depth=0)
            }

        src_tree = build_tree(src, max_depth=max_depth)
        dst_tree = build_tree(dst, max_depth=max_depth)

        self.msg_queue.put(("set_source_tree", src_tree))
        self.msg_queue.put(("set_dest_tree", dst_tree))


# ============================================================
# GUI
# ============================================================

class TransferGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("File Transfer (Threaded)")

        # message queue from worker
        self.msg_queue: queue.Queue = queue.Queue()

        # rule engine (configurable)
        folder_rules = [
            {"pattern": "_ml_data_", "action": "skip"},
        ]
        file_rules = [
            {"ext": ".wav", "pattern": "mic0_data.wav", "action": "allow"},
            {"ext": ".wav", "action": "skip"},
            {"ext": ".pkl", "action": "skip"},
        ]
        self.rule_engine = RuleEngine(folder_rules=folder_rules,
                                      file_rules=file_rules)

        # manager
        self.manager = TransferManager(self.msg_queue, self.rule_engine)

        # state
        self.copying = False
        self.source_dir: Path | None = None
        self.dest_dir: Path | None = None

        # overwrite mode (0=skip existing, 1=overwrite)
        self.overwrite_var = tk.IntVar(value=0)

        # overview depth
        self.overview_depth_var = tk.IntVar(value=2)

        # UI state caches
        self.current_folder: str = ""
        self.current_folder_files: list[str] = []
        self.current_file_rel: str = ""
        self.current_file_name: str = ""
        self.current_file_size: int = 0

        # Track destination tree structure for incremental updates
        # Maps relative path -> tree item ID
        self.dest_tree_items: dict[str, str] = {}
        self.dest_tree_root_id: str = ""

        # Build UI
        self._build_ui()
        self._load_settings()

        # Start polling for worker messages
        self.root.after(100, self._poll_messages)
        self.root.after(500, self._tick_speed)

    # -------------------------------------------
    # UI Creation
    # -------------------------------------------

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # === Config row (source/dest + browse) ===
        cfg_frame = ttk.LabelFrame(main, text="Locations", padding=5)
        cfg_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        ttk.Label(cfg_frame, text="Source:").grid(row=0, column=0, sticky="w")
        self.src_var = tk.StringVar()
        self.src_entry = ttk.Entry(
            cfg_frame, textvariable=self.src_var, width=60)
        self.src_entry.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(cfg_frame, text="Browse...",
                   command=self._browse_source).grid(row=0, column=2, padx=2)

        ttk.Label(cfg_frame, text="Destination:").grid(
            row=1, column=0, sticky="w")
        self.dst_var = tk.StringVar()
        self.dst_entry = ttk.Entry(
            cfg_frame, textvariable=self.dst_var, width=60)
        self.dst_entry.grid(row=1, column=1, sticky="ew", padx=2)
        ttk.Button(cfg_frame, text="Browse...",
                   command=self._browse_dest).grid(row=1, column=2, padx=2)

        # Overwrite options
        overwrite_frame = ttk.Frame(cfg_frame)
        overwrite_frame.grid(row=2, column=0, columnspan=3,
                             sticky="w", pady=(5, 0))
        ttk.Label(overwrite_frame, text="Existing files:").pack(side="left")
        ttk.Radiobutton(
            overwrite_frame, text="Skip existing", variable=self.overwrite_var,
            value=0
        ).pack(side="left", padx=5)
        ttk.Radiobutton(
            overwrite_frame, text="Overwrite", variable=self.overwrite_var,
            value=1
        ).pack(side="left", padx=5)

        # Overview depth selection
        depth_frame = ttk.Frame(cfg_frame)
        depth_frame.grid(row=3, column=0, columnspan=3,
                         sticky="w", pady=(5, 0))
        ttk.Label(depth_frame, text="Directory overview depth:").pack(
            side="left")
        depth_spin = ttk.Spinbox(
            depth_frame,
            from_=1,
            to=10,
            textvariable=self.overview_depth_var,
            width=5
        )
        depth_spin.pack(side="left", padx=5)
        ttk.Label(depth_frame, text="(applies on next Start)").pack(side="left")

        cfg_frame.columnconfigure(1, weight=1)

        # === Split main area into left (filesystem view) and right (status/details/log) ===
        body = ttk.Frame(main)
        body.grid(row=1, column=0, sticky="nsew", pady=5)

        # Left: source/dest filesystem (overview)
        fs_frame = ttk.LabelFrame(
            body, text="Filesystem Overview", padding=5)
        fs_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        ttk.Label(fs_frame, text="Source").grid(row=0, column=0, sticky="w")
        ttk.Label(fs_frame, text="Destination").grid(
            row=0, column=1, sticky="w")

        # Source Tree with scrollbars
        src_tree_frame = ttk.Frame(fs_frame)
        src_tree_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.src_tree = ttk.Treeview(src_tree_frame, columns=("rel",),
                                     show="tree", selectmode="browse")
        src_vscroll = ttk.Scrollbar(src_tree_frame, orient="vertical",
                                    command=self.src_tree.yview)
        src_hscroll = ttk.Scrollbar(src_tree_frame, orient="horizontal",
                                    command=self.src_tree.xview)
        self.src_tree.configure(yscrollcommand=src_vscroll.set,
                                xscrollcommand=src_hscroll.set)
        self.src_tree.grid(row=0, column=0, sticky="nsew")
        src_vscroll.grid(row=0, column=1, sticky="ns")
        src_hscroll.grid(row=1, column=0, sticky="ew")

        src_tree_frame.rowconfigure(0, weight=1)
        src_tree_frame.columnconfigure(0, weight=1)

        # Dest Tree with scrollbars
        dst_tree_frame = ttk.Frame(fs_frame)
        dst_tree_frame.grid(row=1, column=1, sticky="nsew")
        self.dst_tree = ttk.Treeview(dst_tree_frame, columns=("rel",),
                                     show="tree", selectmode="browse")
        dst_vscroll = ttk.Scrollbar(dst_tree_frame, orient="vertical",
                                    command=self.dst_tree.yview)
        dst_hscroll = ttk.Scrollbar(dst_tree_frame, orient="horizontal",
                                    command=self.dst_tree.xview)
        self.dst_tree.configure(yscrollcommand=dst_vscroll.set,
                                xscrollcommand=dst_hscroll.set)
        self.dst_tree.grid(row=0, column=0, sticky="nsew")
        dst_vscroll.grid(row=0, column=1, sticky="ns")
        dst_hscroll.grid(row=1, column=0, sticky="ew")

        dst_tree_frame.rowconfigure(0, weight=1)
        dst_tree_frame.columnconfigure(0, weight=1)

        fs_frame.rowconfigure(1, weight=1)
        fs_frame.columnconfigure(0, weight=1)
        fs_frame.columnconfigure(1, weight=1)

        # Right: status, lists, details, log
        right_frame = ttk.Frame(body)
        right_frame.grid(row=0, column=1, sticky="nsew")

        # Current folder + files + current file
        cur_frame = ttk.LabelFrame(
            right_frame, text="Current folder and file", padding=5)
        cur_frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(cur_frame, text="Current folder (relative):").grid(
            row=0, column=0, sticky="w")
        self.current_folder_var = tk.StringVar()
        ttk.Label(cur_frame, textvariable=self.current_folder_var,
                  foreground="blue").grid(row=0, column=1, sticky="w")

        ttk.Label(cur_frame, text="Files left in folder:").grid(
            row=1, column=0, sticky="w")

        # Current folder files listbox with scrollbars
        files_frame = ttk.Frame(cur_frame)
        files_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.current_folder_files_var = tk.StringVar(value=[])
        self.current_folder_files_lb = tk.Listbox(
            files_frame,
            listvariable=self.current_folder_files_var,
            height=8, width=60
        )
        files_vscroll = ttk.Scrollbar(files_frame, orient="vertical",
                                      command=self.current_folder_files_lb.yview)
        files_hscroll = ttk.Scrollbar(files_frame, orient="horizontal",
                                      command=self.current_folder_files_lb.xview)
        self.current_folder_files_lb.configure(
            yscrollcommand=files_vscroll.set,
            xscrollcommand=files_hscroll.set
        )
        self.current_folder_files_lb.grid(row=0, column=0, sticky="nsew")
        files_vscroll.grid(row=0, column=1, sticky="ns")
        files_hscroll.grid(row=1, column=0, sticky="ew")
        files_frame.rowconfigure(0, weight=1)
        files_frame.columnconfigure(0, weight=1)

        ttk.Label(cur_frame, text="Current file:").grid(
            row=3, column=0, sticky="w")
        self.current_file_var = tk.StringVar()
        ttk.Label(cur_frame, textvariable=self.current_file_var,
                  foreground="purple").grid(row=3, column=1, sticky="w")

        self.current_file_size_var = tk.StringVar()
        ttk.Label(cur_frame, textvariable=self.current_file_size_var).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )

        cur_frame.rowconfigure(2, weight=1)
        cur_frame.columnconfigure(0, weight=1)
        cur_frame.columnconfigure(1, weight=1)

        # Log / debug output
        log_frame = ttk.LabelFrame(
            right_frame, text="Log / Debug Output", padding=5)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(5, 0))

        self.log_text = tk.Text(log_frame, height=10, wrap="none")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_vscroll = ttk.Scrollbar(log_frame, orient="vertical",
                                    command=self.log_text.yview)
        log_vscroll.grid(row=0, column=1, sticky="ns")
        log_hscroll = ttk.Scrollbar(log_frame, orient="horizontal",
                                    command=self.log_text.xview)
        log_hscroll.grid(row=1, column=0, sticky="ew")
        self.log_text.configure(
            yscrollcommand=log_vscroll.set,
            xscrollcommand=log_hscroll.set
        )

        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        # Bottom controls: speed, status, start/stop
        bottom = ttk.Frame(main)
        bottom.grid(row=2, column=0, sticky="ew", pady=(5, 0))

        self.speed_var = tk.StringVar(value="Speed: 0.00 MB/s")
        ttk.Label(bottom, textvariable=self.speed_var).pack(
            side="left", padx=(0, 10))

        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var,
                  foreground="red").pack(side="left")

        btn_frame = ttk.Frame(bottom)
        btn_frame.pack(side="right")
        self.start_btn = ttk.Button(
            btn_frame, text="Start", command=self.start_transfer)
        self.stop_btn = ttk.Button(btn_frame, text="Stop", state="disabled",
                                   command=self.stop_transfer)
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn.pack(side="left", padx=5)

        # overall layout
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)

        right_frame.rowconfigure(0, weight=2)
        right_frame.rowconfigure(1, weight=2)
        right_frame.columnconfigure(0, weight=1)

    # -------------------------------------------
    # Settings (persist last used source/dest)
    # -------------------------------------------

    def _load_settings(self):
        data = load_json(SETTINGS_FILE, default={})
        src = data.get("source_dir", "")
        dst = data.get("dest_dir", "")
        overwrite = data.get("overwrite", 0)
        depth = data.get("overview_depth", 2)
        if src:
            self.src_var.set(src)
            self.source_dir = Path(src)
        if dst:
            self.dst_var.set(dst)
            self.dest_dir = Path(dst)
        self.overwrite_var.set(int(overwrite))
        self.overview_depth_var.set(int(depth))

    def _save_settings(self):
        data = {
            "source_dir": self.src_var.get(),
            "dest_dir": self.dst_var.get(),
            "overwrite": int(self.overwrite_var.get()),
            "overview_depth": int(self.overview_depth_var.get()),
        }
        save_json(SETTINGS_FILE, data)

    # -------------------------------------------
    # Browsing for folders
    # -------------------------------------------

    def _browse_source(self):
        path = filedialog.askdirectory(title="Select Source Folder")
        if path:
            self.src_var.set(path)
            self.source_dir = Path(path)
            self._save_settings()

    def _browse_dest(self):
        path = filedialog.askdirectory(title="Select Destination Folder")
        if path:
            self.dst_var.set(path)
            self.dest_dir = Path(path)
            self._save_settings()

    # -------------------------------------------
    # Worker interaction
    # -------------------------------------------

    def start_transfer(self):
        if self.copying:
            return

        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()

        if not src or not dst:
            messagebox.showerror(
                "Error", "Please select both source and destination.")
            return

        src_path = Path(src)
        dst_path = Path(dst)

        if not src_path.exists() or not src_path.is_dir():
            messagebox.showerror(
                "Error", f"Source is not a valid directory:\n{src}")
            return
        if not dst_path.exists():
            try:
                dst_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror(
                    "Error", f"Cannot create destination:\n{e}")
                return

        self.source_dir = src_path
        self.dest_dir = dst_path
        self._save_settings()

        # Clear UI state
        self.status_var.set("")
        self.log_text.delete("1.0", "end")

        # Configure manager
        self.manager.set_paths(src_path, dst_path)
        self.manager.set_overwrite(bool(self.overwrite_var.get()))
        self.manager.set_overview_depth(self.overview_depth_var.get())

        self.copying = True
        self.start_btn["state"] = "disabled"
        self.stop_btn["state"] = "normal"

        self.manager.start_transfer()

    def stop_transfer(self):
        self.manager.stop()
        # Immediately update UI state - don't wait for worker thread
        self.copying = False
        self.start_btn["state"] = "normal"
        self.stop_btn["state"] = "disabled"

    def _poll_messages(self):
        q = self.msg_queue
        try:
            while True:
                cmd, arg = q.get_nowait()
                self._handle_message(cmd, arg)
        except queue.Empty:
            pass

        self.root.after(100, self._poll_messages)

    def _handle_message(self, cmd, arg):
        if cmd == "error":
            msg = str(arg)
            self.status_var.set(msg)
            self._log(f"[ERROR] {msg}")

        elif cmd == "log":
            self._log(str(arg))

        elif cmd == "set_source_tree":
            self._populate_tree(self.src_tree, arg)

        elif cmd == "set_dest_tree":
            self._populate_tree(self.dst_tree, arg)
            # Track the root ID and initialize the tree items map
            self.dest_tree_items = {}
            children = self.dst_tree.get_children()
            if children:
                self.dest_tree_root_id = children[0]
                self._index_dest_tree(self.dest_tree_root_id, "")

        elif cmd == "dest_item_added":
            self._add_item_to_dest_tree(arg)

        elif cmd == "dest_folder_completed":
            self._update_dest_tree_for_folder(arg)

        elif cmd == "l2_for_l1":
            l1_key = arg.get("l1_key")
            l2_all = arg.get("l2_all", [])
            self._log(f"L1 folder {l1_key} has {len(l2_all)} L2 subfolders.")

        elif cmd == "current_folder":
            self.current_folder = arg
            self.current_folder_var.set(arg)
            self.current_folder_files = []
            self.current_folder_files_var.set([])
            self.current_file_rel = ""
            self.current_file_name = ""
            self.current_file_size = 0
            self.current_file_var.set("")
            self.current_file_size_var.set("")

        elif cmd == "folder_files":
            self.current_folder_files = list(arg)
            self.current_folder_files_var.set(self.current_folder_files)

        elif cmd == "file_start":
            name = arg.get("name", "")
            size = arg.get("size", 0)
            rel = arg.get("rel", "")
            self.current_file_name = name
            self.current_file_size = size
            self.current_file_rel = rel
            self.current_file_var.set(name)
            self.current_file_size_var.set(
                f"File size: {size / (1024 * 1024):.2f} MB"
            )
            self._log(f"Copying: {rel} ({size} bytes)")

        elif cmd == "file_copied":
            rel = arg
            base = os.path.basename(rel)
            if base in self.current_folder_files:
                self.current_folder_files.remove(base)
                self.current_folder_files_var.set(self.current_folder_files)
            self._log(f"Copied: {rel}")

        elif cmd == "folder_done":
            pass  # Folder completion is logged by the worker with summary

        elif cmd == "current_folder_done":
            if self.current_folder == arg:
                self.current_folder = ""
                self.current_folder_var.set("")
                self.current_folder_files = []
                self.current_folder_files_var.set([])
                self.current_file_rel = ""
                self.current_file_name = ""
                self.current_file_size = 0
                self.current_file_var.set("")
                self.current_file_size_var.set("")

        elif cmd == "stopped":
            self.status_var.set("Transfer stopped.")
            self._log("Transfer stopped by user.")
            self.copying = False
            self.start_btn["state"] = "normal"
            self.stop_btn["state"] = "disabled"

        elif cmd == "all_done":
            self.status_var.set("All transfers completed.")
            self._log("All transfers completed.")
            self.copying = False
            self.start_btn["state"] = "disabled"
            self.stop_btn["state"] = "disabled"

    def _tick_speed(self):
        if self.copying and self.manager.start_time:
            speed = self.manager.get_speed() / (1024 * 1024)
            self.speed_var.set(f"Speed: {speed:.2f} MB/s")
        else:
            self.speed_var.set("Speed: 0.00 MB/s")
        self.root.after(500, self._tick_speed)

    # -------------------------------------------
    # Helpers
    # -------------------------------------------

    def _populate_tree(self, tree: ttk.Treeview, tree_data: dict):
        """
        Populate Treeview with full nested tree based on the structure
        produced by _send_directory_overview.
        """
        # Clear existing
        for item in tree.get_children():
            tree.delete(item)

        root_label = tree_data.get("root", "")
        root_id = tree.insert("", "end", text=root_label, open=True)

        def add_children(parent_id, nodes):
            for node in nodes:
                name = node.get("name", "")
                rel = node.get("rel", "")
                item_id = tree.insert(parent_id, "end", text=name, open=False,
                                      values=(rel,))
                children = node.get("children", [])
                if children:
                    add_children(item_id, children)

        add_children(root_id, tree_data.get("children", []))

    def _index_dest_tree(self, item_id: str, rel_path: str):
        """
        Recursively index the destination tree to track which paths exist.
        Maps relative paths to tree item IDs for efficient lookups.
        """
        if rel_path:
            self.dest_tree_items[rel_path] = item_id
        
        # Index children
        for child_id in self.dst_tree.get_children(item_id):
            child_text = self.dst_tree.item(child_id, "text")
            child_values = self.dst_tree.item(child_id, "values")
            if child_values:
                child_rel = child_values[0]
            else:
                # Construct relative path from parent
                if rel_path:
                    child_rel = f"{rel_path}/{child_text}"
                else:
                    child_rel = child_text
            self._index_dest_tree(child_id, child_rel)

    def _add_item_to_dest_tree(self, rel_path: str):
        """
        Incrementally add a path to the destination tree without re-scanning.
        Only adds directory nodes (not files) since the overview shows directories.
        """
        if not self.dest_tree_root_id:
            return
        
        # Split the path into components
        # For a file path like "folder1/folder2/file.txt", we want to add
        # "folder1" and "folder1/folder2" as directory nodes
        path_parts = rel_path.split('/')
        
        # Remove the filename (last part) since we only track directories
        if len(path_parts) > 1:
            # It's a file in a subdirectory
            dir_parts = path_parts[:-1]
        else:
            # It's a file in the root - nothing to add
            return
        
        # Build up the path incrementally and ensure each directory level exists
        current_path = ""
        parent_id = self.dest_tree_root_id
        
        for i, part in enumerate(dir_parts):
            if current_path:
                current_path = f"{current_path}/{part}"
            else:
                current_path = part
            
            # Check if this path already exists in the tree
            if current_path in self.dest_tree_items:
                parent_id = self.dest_tree_items[current_path]
            else:
                # Need to add this directory node
                item_id = self.dst_tree.insert(
                    parent_id, "end", text=part, open=False, values=(current_path,)
                )
                self.dest_tree_items[current_path] = item_id
                parent_id = item_id

    def _update_dest_tree_for_folder(self, folder_key: str):
        """
        Update the destination tree for a completed folder by scanning
        the destination directory and adding/updating tree nodes.
        """
        if not self.dest_dir or not self.dest_tree_root_id:
            return
        
        dst_folder_path = self.dest_dir / folder_key
        if not dst_folder_path.exists() or not dst_folder_path.is_dir():
            return
        
        # Find or create the folder node in the tree
        path_parts = folder_key.split('/')
        parent_id = self.dest_tree_root_id
        current_path = ""
        
        # Navigate to the folder we're updating (creating nodes as needed)
        for i, part in enumerate(path_parts):
            if current_path:
                current_path = f"{current_path}/{part}"
            else:
                current_path = part
            
            # Check if this path exists in the tree
            if current_path in self.dest_tree_items:
                parent_id = self.dest_tree_items[current_path]
            else:
                # Need to create this directory node
                item_id = self.dst_tree.insert(
                    parent_id, "end", text=part, open=False, values=(current_path,)
                )
                self.dest_tree_items[current_path] = item_id
                parent_id = item_id
        
        # Now scan the destination folder and add its subdirectories
        # Get the overview depth from the manager
        max_depth = self.manager.overview_depth
        folder_depth = len(path_parts)
        
        if folder_depth < max_depth:
            # We can still add subdirectories
            remaining_depth = max_depth - folder_depth
            
            def scan_and_add(current_dst_path: Path, current_rel: str, parent_item_id: str, depth: int):
                if depth >= remaining_depth:
                    return
                
                try:
                    with os.scandir(current_dst_path) as it:
                        for entry in it:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            
                            child_rel = f"{current_rel}/{entry.name}" if current_rel else entry.name
                            
                            # Check if already exists
                            if child_rel in self.dest_tree_items:
                                child_id = self.dest_tree_items[child_rel]
                            else:
                                # Add new directory node
                                child_id = self.dst_tree.insert(
                                    parent_item_id, "end", text=entry.name, open=False, values=(child_rel,)
                                )
                                self.dest_tree_items[child_rel] = child_id
                            
                            # Recurse into subdirectories
                            child_path = current_dst_path / entry.name
                            scan_and_add(child_path, child_rel, child_id, depth + 1)
                except Exception:
                    pass
            
            # Start scanning from the completed folder
            scan_and_add(dst_folder_path, folder_key, parent_id, 0)

    def _log(self, text: str):
        self.log_text.insert("end", text + "\n")


# ============================================================
# MAIN
# ============================================================

def main():
    root = tk.Tk()
    app = TransferGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
