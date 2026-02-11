import csv
import os
from pathlib import Path

# --- Parameters ---
input_file_path = r"G:\My Drive\Timesheet\to gcal\2026\2026-02-01 full CSV for import to gcal.csv"
lines_per_file = 500

# Setup paths
input_path = Path(input_file_path)
file_stem = input_path.stem
file_extension = input_path.suffix
output_directory = input_path.parent / "CSV_import_chunks"

# Create the output folder if it doesn't exist
os.makedirs(output_directory, exist_ok=True)

with open(input_path, mode='r', encoding='utf-8', newline='') as f:
    reader = csv.reader(f)
    
    # Extract the header
    header = next(reader)
    
    current_batch = []
    file_count = 1
    
    for row in reader:
        current_batch.append(row)
        
        # When batch is full, write it to a file
        if len(current_batch) == lines_per_file:
            output_filename = f"{file_stem}_pt_{file_count:02d}{file_extension}"
            output_path = output_directory / output_filename
            
            with open(output_path, mode='w', encoding='utf-8', newline='') as out_f:
                writer = csv.writer(out_f)
                writer.writerow(header)
                writer.writerows(current_batch)
            
            print(f"Created: {output_filename}")
            
            # Reset for next batch
            current_batch = []
            file_count += 1

    # Write any remaining rows to a final file
    if current_batch:
        output_filename = f"{file_stem}_pt_{file_count:02d}{file_extension}"
        output_path = output_directory / output_filename
        
        with open(output_path, mode='w', encoding='utf-8', newline='') as out_f:
            writer = csv.writer(out_f)
            writer.writerow(header)
            writer.writerows(current_batch)
            
        print(f"Created: {output_filename}")

print("\nProcess complete.")
print(f"Files are located in: {output_directory}")
