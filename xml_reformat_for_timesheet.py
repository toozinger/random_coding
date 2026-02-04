import xml.etree.ElementTree as ET
import os

def convert_timesheet_xml(input_path):
    DEFAULT_USER_ID = "n7r17k0frHRMyAkHpQPQQZW9cjg1"
    
    # Split the directory from the filename
    file_dir, file_name = os.path.split(input_path)
    
    # Split the filename into name and extension (e.g., "data" and ".xml")
    name, ext = os.path.splitext(file_name)
    
    # Reconstruct the path: directory/name_CONVERTED.extension
    output_path = os.path.join(file_dir, f"{name}_CONVERTED{ext}")
    try:
        tree = ET.parse(input_path)
        old_root = tree.getroot()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    new_root = ET.Element("timesheet")

    # STRICT ORDERING & FIELD SETS based on the latest app versions
    templates = {
        "task": {
            "billable": "true", "billed": "false", "created": "0", "deleted": "false",
            "description": "", "dirty": "true", "distance": "0.0", "endDate": "",
            "feeling": "0", "taskId": "", "lastUpdate": "0", "location": "",
            "locationEnd": "", "paid": "false", "phoneNumber": "", "projectId": "",
            "rateId": "", "signature": "", "startDate": "", "type": "0", "user": DEFAULT_USER_ID
        },
        "project": {
            "color": "-6697984", "created": "0", "deleted": "false", "description": "",
            "dirty": "true", "employer": "", "projectId": "", "lastUpdate": "0",
            "location": "", "order": "0", "salary": "0.0", "salaryVisibility": "0",
            "status": "0", "taskDefaultBillable": "true", "teamId": "", "name": "",
            "user": DEFAULT_USER_ID
        },
        "tag": {
            "color": "-1", "created": "0", "deleted": "false", "dirty": "true",
            "tagId": "", "lastUpdate": "0", "name": "", "teamId": "", "user": DEFAULT_USER_ID
        },
        "taskTag": {
            "created": "0", "deleted": "false", "dirty": "true", "id": "", 
            "lastUpdate": "0", "tagId": "", "taskId": "", "user": DEFAULT_USER_ID
        },
        "break": {
            "created": "0", "deleted": "false", "description": "", "dirty": "true",
            "endDate": "", "breakId": "", "lastUpdate": "0", "startDate": "",
            "taskId": "", "user": DEFAULT_USER_ID
        },
        "note": {
            "created": "0", "deleted": "false", "description": "", "dirty": "true",
            "lastUpdate": "0", "noteId": "", "taskId": "", "user": DEFAULT_USER_ID
        },
        "rate": {
            "created": "0", "deleted": "false", "dirty": "true", "lastUpdate": "0",
            "name": "", "rateId": "", "user": DEFAULT_USER_ID, "value": "0.0"
        }
    }

    structure_map = {
        "breaks": "break", "tasks": "task", "projects": "project",
        "tags": "tag", "notes": "note", "rates": "rate",
        "taskTags": "taskTag"
    }

    bool_fields = ['billable', 'billed', 'paid', 'taskDefaultBillable', 'deleted', 'dirty']

    for container_tag, item_tag in structure_map.items():
        container = old_root.find(container_tag)
        if container is None: continue

        for old_item in container.findall(item_tag):
            new_item = ET.SubElement(new_root, item_tag)
            existing_data = {}
            for child in old_item:
                val = child.text if child.text else ""
                if child.tag in bool_fields:
                    if val == "1": val = "true"
                    elif val == "0": val = "false"
                if child.tag == "user" and not val: val = DEFAULT_USER_ID
                existing_data[child.tag] = val

            if item_tag in templates:
                for field, default_val in templates[item_tag].items():
                    field_elem = ET.SubElement(new_item, field)
                    # If the data exists, use it; otherwise use the template default
                    val = existing_data.get(field, default_val)
                    field_elem.text = val
                    # Important: ensure empty strings are handled as "" and not None
                    if field_elem.text is None: field_elem.text = ""

    def indent(elem, level=0):
        i = "\n" + level * "   "
        if len(elem):
            if not elem.text or not elem.text.strip(): elem.text = i + "   "
            if not elem.tail or not elem.tail.strip(): elem.tail = i
            for elem in elem: indent(elem, level + 1)
            if not elem.tail or not elem.tail.strip(): elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()): elem.tail = i

    indent(new_root)
    new_tree = ET.ElementTree(new_root)
    
    # Write file - using short_empty_elements=False ensures <tag></tag> instead of <tag />
    new_tree.write(output_path, encoding="utf-8", xml_declaration=True, short_empty_elements=False)
    print(f"Complete! Converted: {output_path}")

# Run the script
file_path = r"G:\My Drive\Timesheet\to gcal\2026\2016-05-31 backup.xml"
convert_timesheet_xml(file_path)
