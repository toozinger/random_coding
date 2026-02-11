from icalendar import Calendar
from pathlib import Path

# Configuration
input_file = Path("sleep_data.ics")
output_dir = Path("sleep_chunked")
chunk_size = 1000

# Create the directory if it doesn't exist
output_dir.mkdir(parents=True, exist_ok=True)

# Check if source file exists
if not input_file.exists():
    print(f"Error: {input_file} not found.")
    exit()

# Load and parse the original file
print(f"Loading {input_file}...")
with open(input_file, 'rb') as f:
    original_cal = Calendar.from_ical(f.read())

# Separate events from global components (like Timezones)
events = [comp for comp in original_cal.subcomponents if comp.name == 'VEVENT']
other_components = [comp for comp in original_cal.subcomponents if comp.name != 'VEVENT']

print(f"Total events: {len(events)}. Splitting into groups of {chunk_size}...")

# Split and save
for i in range(0, len(events), chunk_size):
    chunk = events[i : i + chunk_size]
    
    # Create a new calendar for this chunk
    new_cal = Calendar()
    
    # Copy calendar-level metadata (PRODID, VERSION, etc.)
    for key, value in original_cal.items():
        new_cal.add(key, value)
        
    # Add non-event components (like VTIMEZONE) to maintain time accuracy
    for comp in other_components:
        new_cal.add_component(comp)
        
    # Add the 500 events
    for event in chunk:
        new_cal.add_component(event)

    # Save the file
    file_num = (i // chunk_size) + 1
    output_path = output_dir / f"sleep_data_part_{file_num}.ics"
    
    with open(output_path, 'wb') as f:
        f.write(new_cal.to_ical())
    
    print(f"Finished: {output_path}")

print("Done.")
