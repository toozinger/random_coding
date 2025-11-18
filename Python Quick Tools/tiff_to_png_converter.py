from pathlib import Path
from PIL import Image
from tqdm import tqdm
import shutil
import subprocess
import os

def convert_tiff_to_png(folder_path, overwrite=False, test=False, verbose=True):
    """
    Convert .tiff/.tif files in a folder to .png, preserving metadata and optionally 
    overwriting (deleting) originals, supporting robust deletion on Windows.
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        if verbose:
            print(f"'{folder}' is not a valid directory.")
        return

    tiff_files = list(folder.glob('*.tiff')) + list(folder.glob('*.tif'))
    if not tiff_files:
        if verbose:
            print("No .tiff files found.")
        return

    files_to_delete = []
    converted_count = 0
    iterator = tiff_files[:1] if test else tiff_files

    # Convert files
    for tiff_file in tqdm(iterator, desc="Converting .tiff to .png", unit="file"):
        try:
            with Image.open(tiff_file) as img:
                img.load()
                png_file = tiff_file.with_suffix('.png')
                img.save(png_file)
            shutil.copystat(tiff_file, png_file)
            files_to_delete.append(str(tiff_file))
            if verbose:
                print(f"Converted {tiff_file.name} → {png_file.name} (metadata preserved)")
            converted_count += 1
            if test:
                break
        except Exception as e:
            if verbose:
                print(f"Failed to convert {tiff_file.name}: {e}")

    if verbose:
        print(f"Total files converted: {converted_count}")

    # Overwrite mode: robust file deletion
    if overwrite and files_to_delete:
        for filepath in tqdm(files_to_delete, desc="Deleting originals", unit="file"):
            name = Path(filepath).name
            try:
                if os.name == 'nt':
                    # Use cmd /c del for better compatibility with del on Windows
                    result = subprocess.run(['cmd', '/c', 'del', '/f', '/q', filepath],
                                           shell=False, capture_output=True, text=True)
                    if result.returncode == 0 and verbose:
                        print(f"Deleted {name}")
                    elif result.returncode != 0 and verbose:
                        print(f"Failed to delete {name}: {result.stderr}")
                else:
                    result = subprocess.run(['rm', '-f', filepath],
                                           shell=False, capture_output=True, text=True)
                    if result.returncode == 0 and verbose:
                        print(f"Deleted {name}")
                    elif result.returncode != 0 and verbose:
                        print(f"Failed to delete {name}: {result.stderr}")
            except Exception as e:
                print(f"Failed to delete {name}: {e}")

if __name__ == "__main__":
    super_folder = Path(r"F:\Google Drive\Documents\Purdue\GraduateSchool [some folders most updated in Purdue onedrive]\POF_Machine\Testing Data")

    overwrite = 1
    test = 0
    verbose = 0

    # Process the super_folder itself

    print(f"Processing folder: {super_folder}")
     
    # Find all folders recursively (all levels deep)
    subfolders = [f for f in super_folder.rglob('*') if f.is_dir()]

    # Add a loading bar for subfolder processing
    for subfolder in tqdm(subfolders, desc="Processing subfolders", unit="folder"):
        if verbose:
            print(f"\nProcessing subfolder: {subfolder}")
        convert_tiff_to_png(subfolder,
                           overwrite=overwrite,
                           test=test,
                           verbose=verbose)
