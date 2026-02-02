import xml.etree.ElementTree as ET
import os

def convert_timesheet_xml(input_path):
    # Setup output path
    file_dir, file_name = os.path.split(input_path)
    output_path = os.path.join(file_dir, f"CONVERTED_{file_name}")

    try:
        tree = ET.parse(input_path)
        old_root = tree.getroot()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    # Create the new root <timesheet>
    new_root = ET.Element("timesheet")

    # Mapping of Old Container Tag -> Individual Item Tag
    structure_map = {
        "breaks": "break",
        "tasks": "task",
        "projects": "project",
        "tags": "tag",
        "notes": "note",
        "rates": "rate",
        "taskTags": "taskTag",
        "expenses": "expense",
        "automates": "automate"
    }

    # Fields that need 1/0 converted to true/false
    bool_fields = ['billable', 'billed', 'paid', 'taskDefaultBillable', 'deleted', 'dirty']

    for container_tag, item_tag in structure_map.items():
        # Find the container (e.g., <projects>)
        container = old_root.find(container_tag)
        if container is None:
            continue

        # Process each item inside the container (e.g., each <project> inside <projects>)
        for old_item in container.findall(item_tag):
            new_item = ET.SubElement(new_root, item_tag)
            
            # Copy all existing data and convert booleans
            for child in old_item:
                new_child = ET.SubElement(new_item, child.tag)
                val = child.text if child.text else ""
                
                if child.tag in bool_fields:
                    if val == "1":
                        new_child.text = "true"
                    elif val == "0":
                        new_child.text = "false"
                    else:
                        new_child.text = val
                else:
                    new_child.text = val

            # --- ADD MISSING FIELDS BASED ON NEW FORMAT ---
            
            # All items in the new format seem to need a <dirty> tag
            if new_item.find('dirty') is None:
                dirty = ET.SubElement(new_item, 'dirty')
                dirty.text = "true"

            # Project-specific additions
            if item_tag == "project":
                if new_item.find('salaryVisibility') is None:
                    sv = ET.SubElement(new_item, 'salaryVisibility')
                    sv.text = "0"

            # Task-specific additions
            if item_tag == "task":
                for extra in ['phoneNumber', 'signature', 'locationEnd']:
                    if new_item.find(extra) is None:
                        ext_tag = ET.SubElement(new_item, extra)
                        ext_tag.text = ""

    # Helper to make the XML pretty/indented
    def indent(elem, level=0):
        i = "\n" + level * "   "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "   "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for elem in elem:
                indent(elem, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    indent(new_root)
    
    # Write the new XML file
    new_tree = ET.ElementTree(new_root)
    new_tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"Conversion complete! Saved to: {output_path}")

# Run the script
file_path = r"G:\My Drive\Timesheet\to gcal\2026\2018-06-06 backup.xml"
convert_timesheet_xml(file_path)
