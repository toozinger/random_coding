import requests
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from dotenv import load_dotenv
import os
import pythoncom
from win32com.shell import shell, shellcon
import gc
import time
import difflib
import yaml

import re

load_dotenv()  # Load variables from .env file

IMMICH_API_URL = r'http://192.168.1.38:2283/api'
API_KEY = os.getenv('IMMICH_API_KEY')  # Load API key from environment

if not API_KEY:
    raise ValueError("API Key not found. Please set IMMICH_API_KEY in your .env file")


def extract_date_component(name):
    """Extracts a date part from a string in the form yyyy-mm-dd or yyyy. Returns None if not found."""
    # First look for yyyy-mm-dd
    match = re.search(r'(\d{4}-\d{2}-\d{2})', name)
    if match:
        return match.group(1)
    # Fallback: look for just yyyy
    match = re.search(r'(\d{4})', name)
    if match:
        return match.group(1)
    return None


def resolve_shortcut(path):
    """
    Resolve a Windows shortcut (.lnk) file to its target path.

    Returns the resolved target path as a string, or None if cannot resolve.
    """
    if not os.path.exists(path):
        return None
    if not str(path).lower().endswith('.lnk'):
        return str(path)

    try:
        shortcut = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None,
            pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        persist_file = shortcut.QueryInterface(pythoncom.IID_IPersistFile)
        persist_file.Load(str(path))
        target_path, _ = shortcut.GetPath(shell.SLGP_RAWPATH)
        if target_path and os.path.exists(target_path):
            return target_path
    except Exception as e:
        print(f"Failed to resolve shortcut '{path}': {e}")

    return None

class ImmichClient:
    def __init__(self, api_url, api_key, verbose=False, max_retries=5, retry_delay=3):
        self.api_url = api_url
        self.api_key = api_key
        self.headers = {
            'Accept': 'application/json',
            'x-api-key': self.api_key,
        }
        self.verbose = verbose
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def get_albums(self):
        r = requests.get(f'{self.api_url}/albums', headers=self.headers)
        if r.status_code == 200:
            return r.json()
        if self.verbose:
            print(f"Failed to get albums: {r.status_code} {r.text}")
        return []

    def find_album_id(self, target_name):
        albums = self.get_albums()
        for album in albums:
            if album.get('albumName') == target_name:
                return album.get('id')
        return None

    def create_album(self, name, asset_ids=None, description=""):
        payload = {
            "albumName": name,
            "albumUsers": [],
            "assetIds": asset_ids or [],
            "description": description
        }
        hdrs = {**self.headers, 'Content-Type': 'application/json'}
        r = requests.post(f'{self.api_url}/albums', headers=hdrs, json=payload)
        if r.ok:
            return r.json().get('id')
        print(f"Failed to create album '{name}': {r.status_code} {r.text}")
        return None
        
    def find_similar_albums(self, target_name, threshold=0.6):
        albums = self.get_albums()
        matches = []
        target_date = extract_date_component(target_name)
        for album in albums:
            name = album.get('albumName')
            if not name:
                continue
            album_date = extract_date_component(name)
            # If both have date, dates must match exactly
            if album_date and target_date:
                if album_date != target_date:
                    continue  # Dates are different = definitely not same album
            similarity = difflib.SequenceMatcher(None, name.lower(), target_name.lower()).ratio()
            if similarity >= threshold:
                matches.append(name)
        return matches

    def upload_asset(self, file_path):
        stats = file_path.stat()
        data = {
            'deviceAssetId': f'{file_path.name}-{int(stats.st_mtime)}',
            'deviceId': 'python_script',
            'fileCreatedAt': datetime.fromtimestamp(stats.st_mtime).isoformat(),
            'fileModifiedAt': datetime.fromtimestamp(stats.st_mtime).isoformat(),
            'isFavorite': 'false',
        }

        attempts = 0
        while attempts < self.max_retries:
            attempts += 1
            try:
                with open(file_path, 'rb') as f:
                    files = {'assetData': (file_path.name, f, 'application/octet-stream')}
                    r = requests.post(f'{self.api_url}/assets', headers=self.headers, data=data, files=files, timeout=60)
                if r.status_code in (200, 201):
                    return r.json().get('id')
                else:
                    if self.verbose:
                        print(f"Upload fail {file_path.name} [{r.status_code}]: {r.text}")
            except MemoryError as me:
                print(f"MemoryError encountered uploading {file_path.name}, attempt {attempts} of {self.max_retries}")
                gc.collect()
                time.sleep(self.retry_delay)
            except requests.exceptions.RequestException as e:
                if self.verbose:
                    print(f"Request error uploading {file_path.name}: {e}. Retrying ({attempts}/{self.max_retries})")
                time.sleep(self.retry_delay)
            except Exception as e:
                if self.verbose:
                    print(f"Error uploading {file_path.name}: {e}")
                break  # Unexpected error, no retry

        if self.verbose:
            print(f"Failed to upload {file_path.name} after {self.max_retries} attempts.")
        return None

    def add_assets_to_album(self, album_id, asset_ids):
        url = f'{self.api_url}/albums/{album_id}/assets'
        payload = {"ids": asset_ids}
        hdrs = {**self.headers, 'Content-Type': 'application/json'}
        r = requests.put(url, headers=hdrs, json=payload)
        if r.status_code == 200:
            return True
        print(f"Failed to add assets to album {album_id}: {r.status_code} {r.text}")
        return False


def gather_files_from_folder(folder_path, immich: ImmichClient):
    """Collect all valid files in a folder (resolving shortcuts/symlinks)."""
    files = []
    for fp in folder_path.iterdir():
        try:
            if fp.name.startswith('.'):
                continue

            if fp.is_file() and not str(fp).lower().endswith('.lnk'):
                files.append(fp)
            elif str(fp).lower().endswith('.lnk'):
                target = resolve_shortcut(fp)
                if target:
                    target_path = Path(target)
                    if target_path.is_file():
                        files.append(target_path)
                    else:
                        if immich.verbose:
                            print(f"Resolved shortcut does not point to file: {fp}")
                else:
                    if immich.verbose:
                        print(f"Could not resolve shortcut: {fp}")
            elif fp.is_symlink():
                target = fp.resolve(strict=True)
                if target.is_file():
                    files.append(fp)
        except FileNotFoundError:
            if immich.verbose:
                print(f"Skipping broken symlink or missing file: {fp}")
            continue
        except Exception as e:
            if immich.verbose:
                print(f"Error accessing {fp}: {e}")
            continue
    return files

def handle_existing_album(immich: ImmichClient, album_name, folder_path=None, default_choice=None, threshold=0.6, folders_to_review_later=None):


    """Deal with naming/already-existing-album logic with fuzzy matching.
    Returns album ID or None on cancel/skip/defer.

    If default_choice == 'later' and similar albums are found, appends album_name to folders_to_review_later list.
    """

    album_id = immich.find_album_id(album_name)
    if album_id:
        unique_name = f"{album_name}_2"
        if default_choice and default_choice.lower() == 'later':
            print(f"Exact album match for '{album_name}' (ID: {album_id}). Deferring review of this folder to later.")
            if folders_to_review_later is not None:
                folders_to_review_later.append(str(folder_path))
            return None
        elif default_choice and default_choice.lower() in ['y', 'n', 's']:
            choice = default_choice.lower()
            print(f"Default choice '{choice}' selected for album '{album_name}'.")
        else:
            while True:
                choice = input(
                    f"An album named '{album_name}' already exists (ID: {album_id}).\n"
                    f"Would you like to add photos to this album (y), "
                    f"or create a new album named '{unique_name}' (n), "
                    "or cancel/skip (s): "
                ).strip().lower()
                if choice in ["y", "n", "s"]:
                    break
                print("Please enter 'y', 'n', or 's'.")

        if choice == 'y':
            return album_id

        if choice == 'n':
            print(f"Creating new album: {unique_name}")
            new_album_id = immich.create_album(unique_name)
            if not new_album_id:
                print("Album creation failed, aborting.")
            return new_album_id

        print("Skipping.")
        return None  # Cancel or skip

    similar_albums = immich.find_similar_albums(album_name, threshold=threshold)

    if similar_albums:
        if default_choice is None:
            print(f"Fuzzy matched similar albums for '{album_name}':")
            for name in similar_albums:
                print(f"  - {name}")
            print(f"Currently uploaded folder: {album_name}")
            print("Skipping due to similarity with existing albums.")
            return None  # Skip this folder / album

        elif default_choice.lower() == "later":
            print(f"Fuzzy matched similar albums for '{album_name}':")
            for name in similar_albums:
                print(f"  - {name}")
            print(f"Currently uploaded folder: {album_name}")
            print("Deferring review of this folder to later.")
            if folders_to_review_later is not None:
                folders_to_review_later.append(str(folder_path))
            return None  # Skip for now but record for later

        else:
            print(f"Warning: Found similar albums for '{album_name}' but proceeding due to default choice '{default_choice}'.")

    return None

def import_single_folder_as_album(folder_path, immich: ImmichClient, default_choice=None, threshold=0.6, folders_to_review_later=None):
    """Imports the given folder as one album. Handles prompting for name conflicts."""

    album_name = folder_path.name
    print(f"Preparing to import to album: {album_name}")

    # Check for album existence/similarity FIRST!
    album_id = handle_existing_album(immich, album_name, folder_path=folder_path,
                                     default_choice=default_choice, folders_to_review_later=folders_to_review_later, threshold=threshold)
    if album_id is None:
        # The album didn't exist or user cancelled or deferred or conflict
        # If a review is needed, it's already been added to folders_to_review_later.
        # Only create album if not skipping/deferred
        if (folders_to_review_later is not None) and (album_name in folders_to_review_later):
            print(f"Skipping {album_name}; deferred for review later.")
            return
        # Try to create new album (if strictly needed)
        if immich.find_album_id(album_name) is None:
            album_id = immich.create_album(album_name)
        # If album_id is still None, abort
        if album_id is None:
            print("Skipping (user cancelled, deferred, or album creation failed).")
            return

    files = gather_files_from_folder(folder_path, immich)
    if not files:
        print(f"No files or valid shortcuts/symlinks found in folder '{folder_path}', aborting.")
        return

    asset_ids = []
    for i, fp in enumerate(tqdm(files, desc=f"Uploading assets from {album_name}", unit="file")):
        aid = immich.upload_asset(fp)
        if aid:
            asset_ids.append(aid)
        if i > 0 and i % 10 == 0:
            gc.collect()

    if not asset_ids:
        print(f"No assets uploaded from folder '{folder_path}', aborting.")
        return

    success = immich.add_assets_to_album(album_id, asset_ids)
    if success:
        print(f"Import completed for album '{album_name}'.")
    else:
        print(f"Failed to add assets to album '{album_name}'.")

def import_folder_as_album(folder_path, client, default_choice=None, threshold=0.6):
    """
    Import this folder (and optionally all subfolders) as albums.
    Only bottom-level folders containing files (and no subfolders) will be imported.
    Returns the list of folders requiring review due to album name conflicts.
    """

    folder_path = Path(folder_path)
    if not folder_path.is_dir():
        print("Invalid directory:", folder_path)
        return []

    print(f"Working on base folder recursively: {folder_path}")

    # Get all directories recursively
    all_dirs = [folder_path] + [p for p in folder_path.rglob('*') if p.is_dir()]

    # Filter to only bottom folders that contain files and no subdirectories
    all_folders = []
    for folder in all_dirs:
        try:
            entries = list(folder.iterdir())
        except Exception as e:
            if client.verbose:
                print(f"Cannot access {folder}: {e}")
            continue

        has_files = any(f.is_file() for f in entries)
        has_subdirs = any(d.is_dir() for d in entries)
        if has_files and not has_subdirs:
            all_folders.append(folder)

    folders_to_review_later = []

    total = len(all_folders)
    for i, folder in enumerate(all_folders, start=1):
        print(f"{'*' * 20}>>> [{i}/{total}] Processing folder: {folder}")
        import_single_folder_as_album(folder, client, default_choice=default_choice, folders_to_review_later=folders_to_review_later, threshold=threshold)
        gc.collect()

    if folders_to_review_later:
        print("\nFolders deferred for later review due to fuzzy or exact album name matches:")
        for folder_name in folders_to_review_later:
            print(f"  - {folder_name}")
    else:
        print("No folders needed review (no fuzzy or duplicate album names encountered).")

    return folders_to_review_later

if __name__ == "__main__":

    # Default user choice for album conflict: 'y' - add to existing album
    default_choice = "later"

    # Set your target folder path here
    folder_path = r"B:\upload_batch2"
    threshold = 0.8

    client = ImmichClient(IMMICH_API_URL, API_KEY, verbose=False)

    folders_to_review = import_folder_as_album(folder_path, client, default_choice=default_choice, threshold=threshold)

    print("All folders processed.")

    if folders_to_review:
        print("\nFolders to review (conflicts/deferred):")
        for folder_path in folders_to_review:
            print(f"  - {folder_path}")
        
        # Generate datetime string
        dt_string = datetime.now().strftime("%Y%m%d_%H%M%S")
        yaml_path = f"folders_to_review_{dt_string}.yaml"
    
        # Write folders to YAML
        with open(yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump({'folders_to_review': folders_to_review}, f, default_flow_style=False, allow_unicode=True)
        print(f"\nSaved folders to review to {yaml_path}")
    
    else:
        print("\nNo folders need review.")