import os
import time
from pathlib import Path
from PIL import Image, ImageOps
from tqdm import tqdm

# Parameters
input_folder = Path(r"F:\Google Drive\Documents\Photos\Chestnut\auto_chestnut")
output_folder = input_folder / "converted_webp"
overwrite = 0
verbose = 0
max_resolution = (1920, 1080)
webp_quality = 80

os.makedirs(output_folder, exist_ok=True)

image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}

def reduce_file_size_save_webp(input_path: Path, output_path: Path):
    start_total = time.perf_counter()

    with Image.open(input_path) as img:
        # Step 1: Apply orientation to pixels first
        start = time.perf_counter()
        img = ImageOps.exif_transpose(img)
        step1 = time.perf_counter() - start

        # Step 2: Extract EXIF after transpose (orientation tag is now removed)
        start = time.perf_counter()
        exif_data = img.info.get('exif', None)
        step2 = time.perf_counter() - start

        # Step 3: Resize
        start = time.perf_counter()
        img.thumbnail(max_resolution, Image.LANCZOS)
        step3 = time.perf_counter() - start

        # Step 4: Convert and save with cleaned EXIF
        start = time.perf_counter()
        img = img.convert('RGB')
        save_kwargs = {'format': 'WEBP', 'quality': webp_quality, 'method': 1}
        if exif_data:
            save_kwargs['exif'] = exif_data
        img.save(output_path, **save_kwargs)
        step4 = time.perf_counter() - start

    total_time = time.perf_counter() - start_total

    if verbose:
        print(f"[TIMING] Transpose: {step1:.3f}s | Extract EXIF: {step2:.3f}s | Resize: {step3:.3f}s | Save: {step4:.3f}s | Total: {total_time:.3f}s")

# Rest of your code...
images_to_convert = [f for f in input_folder.iterdir()
                     if f.is_file() and f.suffix.lower() in image_extensions]

for file_path in tqdm(images_to_convert, desc="Converting images to WEBP"):
    output_file = output_folder / (file_path.stem + ".webp")
    if output_file.exists() and not overwrite:
        if verbose:
            tqdm.write(f"Skipping (exists): {output_file}")
        continue

    try:
        reduce_file_size_save_webp(file_path, output_file)
        if verbose:
            tqdm.write(f"Converted: {file_path.name} -> {output_file.name}")
    except Exception as e:
        tqdm.write(f"Error converting {file_path.name}: {e}")
