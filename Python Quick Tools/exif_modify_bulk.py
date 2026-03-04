import os
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor
from exif_modify import ImageTask, ProcessorThread, get_existing_metadata
from PyQt5.QtCore import QCoreApplication
import sys

# --- Configuration ---
ROOT_FOLDER = r"C:\Users\toozi\Downloads\2007"
VALID_EXTENSIONS = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png', '.webp')
# Use 12 of your 14 threads to keep the system responsive
MAX_WORKERS = 12 

def process_file_worker(task_data):
    """
    This function runs in a completely separate CPU process.
    """
    file_path, root, output_folder = task_data
    
    # We initialize a local processor inside the worker process
    # to avoid pickling issues with PyQt objects.
    processor = ProcessorThread()
    
    try:
        y, m, d, desc = get_existing_metadata(file_path, output_folder)
        task = ImageTask(
            file_path=file_path,
            output_folder=output_folder,
            folder_path=root,
            y=y, m=m, d=d, 
            description=desc, 
            rotation=0
        )
        processor.process_image(task)
        return True
    except Exception as e:
        return f"Error on {os.path.basename(file_path)}: {e}"

def run_headless_batch():
    print(f"Scanning directory: {ROOT_FOLDER}...")
    work_queue = []
    
    for root, dirs, files in os.walk(ROOT_FOLDER):
        if "processed" in dirs:
            dirs.remove("processed")
        
        valid_files = [f for f in files if f.lower().endswith(VALID_EXTENSIONS)]
        if valid_files:
            output_folder = os.path.join(root, "processed")
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)
            
            for f in valid_files:
                work_queue.append((os.path.join(root, f), root, output_folder))

    print(f"Distributing {len(work_queue)} images across {MAX_WORKERS} CPU cores...")

    # ProcessPoolExecutor effectively clones your script into 12 workers
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(tqdm(executor.map(process_file_worker, work_queue), 
                  total=len(work_queue), 
                  desc="Parallel Processing", 
                  unit="img"))

    print("\nProcessing complete.")

if __name__ == "__main__":

    # Creates a non-GUI app instance so QThreads/Signals don't crash
    app = QCoreApplication(sys.argv) 
    run_headless_batch()