# -*- coding: utf-8 -*-
"""
Created on Sat Jul  5 17:02:40 2025

@author: Toozinger
"""

import cv2
import os
import datetime
from tqdm import tqdm

# Folder containing images
image_folder = r"F:\Google Drive\Documents\Photos\Chestnut\auto_chestnut"

# Output video file
output_video = r"F:\Google Drive\Documents\Photos\Chestnut\timelapse.mp4"

# Frames per second
fps = 30  # Change this value as needed

# Get list of image files
valid_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".tiff")
images = [img for img in os.listdir(image_folder) if img.lower().endswith(valid_extensions)]

# Sort images alphabetically
images = sorted(images)

if not images:
    raise ValueError("No images found in the specified folder.")

# Read the first image to get size
first_image_path = os.path.join(image_folder, images[0])
frame = cv2.imread(first_image_path)
if frame is None:
    raise IOError(f"Cannot read the first image file: {first_image_path}")

# Resize images smaller for smaller output file size (adjust as needed)
scale_percent = 50  # percent of original size (e.g., 50%)
width = int(frame.shape[1] * scale_percent / 100)
height = int(frame.shape[0] * scale_percent / 100)
size = (width, height)

# Choose codec - mp4v for mp4 output, widely supported without extra DLLs
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter(output_video, fourcc, fps, size)

try:
    for image_name in tqdm(images, desc="Processing images", unit="img"):
        img_path = os.path.join(image_folder, image_name)
        img = cv2.imread(img_path)
        if img is None:
            print(f"Warning: Skipping {img_path} (cannot read image)")
            continue

        # Resize image to target size
        img = cv2.resize(img, size)

        # Get last modified time of the image
        mod_timestamp = os.path.getmtime(img_path)
        mod_dt = datetime.datetime.fromtimestamp(mod_timestamp)

        # Format datetime as "YYYY-MM-DD hh:mm(am/pm)"
        datetime_text = mod_dt.strftime('%Y-%m-%d %I:%M %p').lower()

        # Put the text on the image in bottom-left corner
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 1
        color = (255, 255, 255)  # white text
        thickness = 2
        margin = 10

        # Calculate text size for background rectangle
        (text_width, text_height), baseline = cv2.getTextSize(datetime_text, font, font_scale, thickness)
        x = margin
        y = size[1] - margin

        # Draw black rectangle background for better visibility
        cv2.rectangle(img,
                      (x - 5, y - text_height - 5),
                      (x + text_width + 5, y + baseline + 5),
                      (0, 0, 0),
                      thickness=cv2.FILLED)

        # Draw text over the rectangle
        cv2.putText(img, datetime_text, (x, y), font, font_scale, color, thickness, cv2.LINE_AA)

        out.write(img)

except KeyboardInterrupt:
    print("\nProcess interrupted by user. Saving the video up to the current frame...")

finally:
    out.release()
    print(f"Timelapse video saved to {output_video}")
