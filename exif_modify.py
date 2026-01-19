import os
import sys
import yaml
from pathlib import Path
from platformdirs import user_config_dir
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt
from PIL import Image
import piexif
import piexif.helper

class ImageDescriber(QWidget):
    def __init__(self):
        super().__init__()
        
        # Setup Config Path
        self.config_dir = Path(user_config_dir("PythonTools", "meta_data_modify"))
        self.config_file = self.config_dir / "persistence.yaml"
        
        # Default state
        self.folder_path = self.load_last_path()
        self.files = []
        self.current_index = 0
        self.last_date = {"y": "", "m": "", "d": ""} 

        self.initUI()
        
        if self.folder_path and os.path.exists(self.folder_path):
            self.scan_folder(self.folder_path)

    def initUI(self):
        self.setWindowTitle('Photo Metadata Tagger')
        self.setGeometry(100, 100, 900, 900)

        self.main_layout = QVBoxLayout()

        # --- Top Bar: Folder Selection ---
        self.top_layout = QHBoxLayout()
        self.path_label = QLabel(self.folder_path if self.folder_path else "No folder selected")
        self.path_label.setStyleSheet("color: #666; font-style: italic;")
        self.browse_btn = QPushButton("Browse Folder")
        self.browse_btn.clicked.connect(self.select_folder)
        self.top_layout.addWidget(self.path_label, 1)
        self.top_layout.addWidget(self.browse_btn)
        self.main_layout.addLayout(self.top_layout)

        # Progress Label
        self.progress_label = QLabel("Please select a folder to begin")
        self.main_layout.addWidget(self.progress_label)

        # Image Preview
        self.image_label = QLabel("No Image Loaded")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(600, 450)
        self.image_label.setScaledContents(False) 
        self.main_layout.addWidget(self.image_label)

        # Filename Label
        self.file_info_label = QLabel("")
        self.file_info_label.setAlignment(Qt.AlignCenter)
        self.file_info_label.setStyleSheet("font-weight: bold; color: #555; font-size: 14px;")
        self.main_layout.addWidget(self.file_info_label)

        # --- Date Input Section ---
        date_section_layout = QVBoxLayout()
        date_label = QLabel("Date Taken (Year / Month / Day):")
        date_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        date_section_layout.addWidget(date_label)

        date_group_layout = QHBoxLayout()
        self.year_input = QLineEdit()
        self.year_input.setPlaceholderText("YYYY")
        self.year_input.setFixedWidth(100)
        
        self.month_input = QLineEdit()
        self.month_input.setPlaceholderText("MM")
        self.month_input.setFixedWidth(100)
        
        self.day_input = QLineEdit()
        self.day_input.setPlaceholderText("DD")
        self.day_input.setFixedWidth(100)

        date_group_layout.addWidget(self.year_input)
        date_group_layout.addWidget(self.month_input)
        date_group_layout.addWidget(self.day_input)
        date_group_layout.addStretch()
        
        date_section_layout.addLayout(date_group_layout)
        self.main_layout.addLayout(date_section_layout)

        # Description Input
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Type description (Title) here...")
        self.desc_input.setFixedHeight(40)
        self.desc_input.returnPressed.connect(self.process_and_next)
        self.main_layout.addWidget(self.desc_input)

        # --- Navigation Buttons ---
        self.nav_layout = QHBoxLayout()
        
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedWidth(100)
        self.back_btn.setFixedHeight(50)
        self.back_btn.clicked.connect(self.prev_image)
        
        self.forward_btn = QPushButton("Forward →")
        self.forward_btn.setFixedWidth(100)
        self.forward_btn.setFixedHeight(50)
        self.forward_btn.clicked.connect(self.forward_image)

        self.next_btn = QPushButton("Process & Next →")
        self.next_btn.setFixedHeight(50)
        self.next_btn.setStyleSheet("background-color: #2196F3; color: white; font-size: 16px; font-weight: bold;")
        self.next_btn.clicked.connect(self.process_and_next)
        
        self.nav_layout.addWidget(self.back_btn)
        self.nav_layout.addWidget(self.forward_btn)
        self.nav_layout.addWidget(self.next_btn)
        self.main_layout.addLayout(self.nav_layout)

        self.setLayout(self.main_layout)

    def load_last_path(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    data = yaml.safe_load(f)
                    return data.get("last_path", "")
        except Exception as e:
            print(f"Error loading config: {e}")
        return ""

    def save_last_path(self):
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w') as f:
                yaml.dump({"last_path": self.folder_path}, f)
        except Exception as e:
            print(f"Error saving config: {e}")

    def select_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Directory", self.folder_path)
        if dir_path:
            self.scan_folder(dir_path)

    def scan_folder(self, folder_path):
        self.folder_path = folder_path
        self.path_label.setText(folder_path)
        self.save_last_path()
        
        self.output_folder = os.path.join(folder_path, "processed")
        if not os.path.exists(self.output_folder):
            os.makedirs(self.output_folder)

        valid_extensions = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png')
        self.files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(valid_extensions)])
        self.current_index = 0
        self.load_image()

    def load_image(self):
        if 0 <= self.current_index < len(self.files):
            self.desc_input.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.forward_btn.setEnabled(True)
            self.back_btn.setEnabled(self.current_index > 0)
            self.next_btn.setText("Process & Next →")
            self.next_btn.setStyleSheet("background-color: #2196F3; color: white; font-size: 16px; font-weight: bold;")

            filename = self.files[self.current_index]
            original_file_path = os.path.join(self.folder_path, filename)
            processed_file_path = os.path.join(self.output_folder, f"{os.path.splitext(filename)[0]}.jpg")
            is_processed = os.path.exists(processed_file_path)
            read_path = processed_file_path if is_processed else original_file_path
            
            self.progress_label.setText(f"Image {self.current_index + 1} of {len(self.files)}")
            self.file_info_label.setText(f"File: {filename} {'(Edited)' if is_processed else ''}")
            
            self.desc_input.clear()
            self.year_input.clear()
            self.month_input.clear()
            self.day_input.clear()

            found_exif_date = False
            try:
                img = Image.open(read_path)
                if "exif" in img.info:
                    exif_dict = piexif.load(img.info["exif"])
                    
                    # Load Date
                    dt_str = None
                    if piexif.ExifIFD.DateTimeOriginal in exif_dict.get("Exif", {}):
                        dt_str = exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal].decode('utf-8')
                    elif piexif.ImageIFD.DateTime in exif_dict.get("0th", {}):
                        dt_str = exif_dict["0th"][piexif.ImageIFD.DateTime].decode('utf-8')
                    
                    if dt_str:
                        try:
                            date_part = dt_str.split(' ')[0]
                            y, m, d = date_part.split(':')
                            self.year_input.setText(y)
                            self.month_input.setText(m)
                            self.day_input.setText(d)
                            self.last_date = {"y": y, "m": m, "d": d}
                            found_exif_date = True
                        except: pass

                    # LOAD TITLE (Description)
                    if piexif.ImageIFD.ImageDescription in exif_dict.get("0th", {}):
                        try:
                            desc = exif_dict["0th"][piexif.ImageIFD.ImageDescription].decode('utf-8')
                            self.desc_input.setText(desc)
                        except: pass
                    elif piexif.ImageIFD.XPTitle in exif_dict.get("0th", {}):
                        try:
                            desc = exif_dict["0th"][piexif.ImageIFD.XPTitle].decode('utf-16le').strip('\x00')
                            self.desc_input.setText(desc)
                        except: pass

                if not found_exif_date:
                    self.year_input.setText(self.last_date["y"])
                    self.month_input.setText(self.last_date["m"])
                    self.day_input.setText(self.last_date["d"])

                pixmap = QPixmap(original_file_path)
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(self.image_label.width(), self.image_label.height(), 
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.image_label.setPixmap(scaled_pixmap)
                else:
                    self.image_label.setText("Error loading preview.")
            except Exception as e:
                print(f"Error loading: {e}")
                self.image_label.setText("Error opening file.")

            self.desc_input.setFocus()

        elif len(self.files) > 0 and self.current_index >= len(self.files):
            self.image_label.clear()
            self.image_label.setText("Processing Complete!")
            self.file_info_label.setText("All files in folder have been handled.")
            self.desc_input.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.forward_btn.setEnabled(False)
            self.back_btn.setEnabled(True)

    def prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_image()

    def forward_image(self):
        if self.current_index < len(self.files):
            self.current_index += 1
            self.load_image()

    def process_and_next(self):
        if self.current_index >= len(self.files):
            return

        self.next_btn.setEnabled(False)
        self.next_btn.setText("Processing...")
        QApplication.processEvents()

        filename = self.files[self.current_index]
        file_path = os.path.join(self.folder_path, filename)
        description = self.desc_input.text().strip()
        
        y, m, d = self.year_input.text().strip(), self.month_input.text().strip(), self.day_input.text().strip()
        if y or m or d:
            self.last_date = {"y": y, "m": m, "d": d}

        try:
            img = Image.open(file_path)
            if hasattr(img, '_getexif') and img._getexif():
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            exif_dict = {"0th": {}, "Exif": {}, "1st": {}, "thumbnail": None, "GPS": {}}
            
            # Save Date
            if y or m or d:
                exif_date = f"{y if y else '1900'}:{m.zfill(2) if m else '01'}:{d.zfill(2) if d else '01'} 12:00:00"
                exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_date
                exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_date
                exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = exif_date
            
            # SAVE TITLE
            if description:
                # Standard Exif Title/Description
                exif_dict['0th'][piexif.ImageIFD.ImageDescription] = description.encode('utf-8')
                # Windows Specific Title
                exif_dict['0th'][piexif.ImageIFD.XPTitle] = description.encode('utf-16le')

            exif_bytes = piexif.dump(exif_dict)
            save_path = os.path.join(self.output_folder, f"{os.path.splitext(filename)[0]}.jpg")
            img.save(save_path, "JPEG", optimize=True, exif=exif_bytes, quality=90)

        except Exception as e:
            print(f"Error processing {filename}: {e}")

        self.current_index += 1
        self.load_image()

    def resizeEvent(self, event):
        if self.files and self.current_index < len(self.files):
            self.load_image()
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.save_last_path()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = ImageDescriber()
    ex.show()
    sys.exit(app.exec_())
