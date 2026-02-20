import os
import sys
import yaml
from pathlib import Path
from platformdirs import user_config_dir
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog, 
                             QGraphicsView, QGraphicsScene, QFrame)
from PyQt5.QtGui import QPixmap, QPainter, QImageReader
from PyQt5.QtCore import Qt, QRectF
from PIL import Image, ImageOps
import piexif
import piexif.helper

class PhotoViewer(QGraphicsView):
    """A custom graphics view for zooming and panning."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 0
        self._empty = True
        self._scene = QGraphicsScene(self)
        self._photo = self._scene.addPixmap(QPixmap())
        self.setScene(self._scene)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(Qt.black)
        self.setFrameShape(QFrame.NoFrame)

    def hasPhoto(self):
        return not self._empty

    def setPhoto(self, pixmap=None):
        self._zoom = 0
        if pixmap and not pixmap.isNull():
            self._empty = False
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._photo.setPixmap(pixmap)
        else:
            self._empty = True
            self.setDragMode(QGraphicsView.NoDrag)
            self._photo.setPixmap(QPixmap())
        self.fitInView()

    def fitInView(self, scale=True):
        rect = QRectF(self._photo.pixmap().rect())
        if not rect.isNull():
            self.setSceneRect(rect)
            if self.hasPhoto():
                unity = self.transform().mapRect(QRectF(0, 0, 1, 1))
                self.scale(1 / unity.width(), 1 / unity.height())
                viewrect = self.viewport().rect()
                scenerect = self.transform().mapRect(rect)
                factor = min(viewrect.width() / scenerect.width(),
                             viewrect.height() / scenerect.height())
                self.scale(factor, factor)
            self._zoom = 0

    def wheelEvent(self, event):
        if self.hasPhoto():
            if event.angleDelta().y() > 0:
                factor = 1.25
                self._zoom += 1
            else:
                factor = 0.8
                self._zoom -= 1
            
            if self._zoom > 0:
                self.scale(factor, factor)
            elif self._zoom == 0:
                self.fitInView()
            else:
                self._zoom = 0

class ImageDescriber(QWidget):
    def __init__(self):
        super().__init__()
        
        self.config_dir = Path(user_config_dir("PythonTools", "meta_data_modify"))
        self.config_file = self.config_dir / "persistence.yaml"
        
        self.folder_path = self.load_last_path()
        self.files = []
        self.current_index = 0
        self.last_date = {"y": "", "m": "", "d": ""} 

        self.initUI()
        
        if self.folder_path and os.path.exists(self.folder_path):
            self.scan_folder(self.folder_path)

    def initUI(self):
        self.setWindowTitle('Photo Metadata Tagger (Zoom & Pan enabled)')
        self.setGeometry(100, 100, 1000, 900)
        self.main_layout = QVBoxLayout()

        # Top Controls
        self.top_layout = QHBoxLayout()
        self.path_label = QLabel(self.folder_path if self.folder_path else "No folder selected")
        self.path_label.setStyleSheet("color: #666; font-style: italic;")
        self.browse_btn = QPushButton("Browse Folder")
        self.browse_btn.clicked.connect(self.select_folder)
        self.top_layout.addWidget(self.path_label, 1)
        self.top_layout.addWidget(self.browse_btn)
        self.main_layout.addLayout(self.top_layout)

        self.progress_label = QLabel("Please select a folder to begin")
        self.main_layout.addWidget(self.progress_label)

        # Dynamic Image Viewer
        self.viewer = PhotoViewer(self)
        self.main_layout.addWidget(self.viewer, 1) # Factor 1 allows it to expand

        self.file_info_label = QLabel("")
        self.file_info_label.setAlignment(Qt.AlignCenter)
        self.file_info_label.setStyleSheet("font-weight: bold; color: #555; font-size: 14px;")
        self.main_layout.addWidget(self.file_info_label)

        # Date and Description Inputs
        date_section_layout = QVBoxLayout()
        date_group_layout = QHBoxLayout()
        self.year_input = QLineEdit()
        self.year_input.setPlaceholderText("YYYY")
        self.month_input = QLineEdit()
        self.month_input.setPlaceholderText("MM")
        self.day_input = QLineEdit()
        self.day_input.setPlaceholderText("DD")

        for inp in [self.year_input, self.month_input, self.day_input]:
            inp.setFixedWidth(80)
            date_group_layout.addWidget(inp)
            
        date_group_layout.addStretch()
        date_section_layout.addLayout(date_group_layout)
        self.main_layout.addLayout(date_section_layout)

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Type description here...")
        self.desc_input.setFixedHeight(40)
        self.desc_input.returnPressed.connect(self.process_and_next)
        self.main_layout.addWidget(self.desc_input)

        # Navigation
        self.nav_layout = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.forward_btn = QPushButton("Forward →")
        self.ff_btn = QPushButton("Fast Forward ⏩")
        self.next_btn = QPushButton("Process & Convert →")
        
        self.back_btn.clicked.connect(self.prev_image)
        self.forward_btn.clicked.connect(self.forward_image)
        self.ff_btn.clicked.connect(self.fast_forward)
        self.next_btn.clicked.connect(self.process_and_next)

        self.ff_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        self.next_btn.setStyleSheet("background-color: #2196F3; color: white; font-size: 16px; font-weight: bold;")
        
        for btn in [self.back_btn, self.forward_btn, self.ff_btn, self.next_btn]:
            btn.setFixedHeight(50)
            self.nav_layout.addWidget(btn)

        self.main_layout.addLayout(self.nav_layout)
        self.setLayout(self.main_layout)

    def load_last_path(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    data = yaml.safe_load(f)
                    return data.get("last_path", "")
        except: pass
        return ""

    def save_last_path(self):
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w') as f:
                yaml.dump({"last_path": self.folder_path}, f)
        except: pass

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

        valid_extensions = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png', '.webp')
        self.files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(valid_extensions)])
        self.current_index = 0
        self.load_image()

    def find_best_meta_source(self, base_name, original_ext):
        processed_path = os.path.join(self.output_folder, base_name + ".jpg")
        if os.path.exists(processed_path): return processed_path
        local_webp = os.path.join(self.folder_path, base_name + ".webp")
        if os.path.exists(local_webp): return local_webp
        return os.path.join(self.folder_path, base_name + original_ext)

    def load_image(self):
        self.next_btn.setText("Process & Convert →")
        
        if not self.files or self.current_index >= len(self.files):
            self.viewer.setPhoto(None)
            self.progress_label.setText("Finished")
            return

        filename = self.files[self.current_index]
        file_path = os.path.join(self.folder_path, filename)
        base_name, ext = os.path.splitext(filename)
        meta_read_path = self.find_best_meta_source(base_name, ext)
        
        self.progress_label.setText(f"Image {self.current_index + 1} of {len(self.files)}")
        self.file_info_label.setText(f"File: {filename}")
        self.desc_input.clear()

        # Handle Exif Data
        try:
            img = Image.open(meta_read_path)
            if "exif" in img.info:
                exif_dict = piexif.load(img.info["exif"])
                dt_str = exif_dict.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
                if dt_str:
                    date_part = dt_str.decode('utf-8').split(' ')[0]
                    y, m, d = date_part.split(':')
                    self.year_input.setText(y)
                    self.month_input.setText(m)
                    self.day_input.setText(d)
                
                desc = exif_dict.get("0th", {}).get(piexif.ImageIFD.ImageDescription)
                if desc: self.desc_input.setText(desc.decode('utf-8'))
            img.close()

            pixmap = QPixmap(file_path)
            self.viewer.setPhoto(pixmap)
        except Exception as e:
            print(f"Error loading: {e}")

        self.desc_input.setFocus()

    def process_and_next(self):
        if self.current_index >= len(self.files): return
        
        self.next_btn.setEnabled(False)
        filename = self.files[self.current_index]
        file_path = os.path.join(self.folder_path, filename)
        base_name, ext = os.path.splitext(filename)
        
        y, m, d = self.year_input.text(), self.month_input.text(), self.day_input.text()
        description = self.desc_input.text().strip()

        try:
            img = Image.open(file_path)
            img = ImageOps.exif_transpose(img)
            exif_dict = {"0th": {}, "Exif": {}, "1st": {}, "thumbnail": None, "GPS": {}}
            
            exif_date = f"{y if y else '1900'}:{m.zfill(2) if m else '01'}:{d.zfill(2) if d else '01'} 12:00:00"
            exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_date
            exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_date
            
            if description:
                exif_dict['0th'][piexif.ImageIFD.ImageDescription] = description.encode('utf-8')

            exif_bytes = piexif.dump(exif_dict)
            
            # Save Lossless WebP
            img.save(os.path.join(self.folder_path, base_name + ".webp"), "WEBP", lossless=True, exif=exif_bytes)
            # Save Processed JPG
            img.convert("RGB").save(os.path.join(self.output_folder, base_name + ".jpg"), "JPEG", quality=90, exif=exif_bytes)
            
            img.close()
            self.current_index += 1
        except Exception as e:
            print(f"Error: {e}")
            self.current_index += 1

        self.next_btn.setEnabled(True)
        self.load_image()

    def prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_image()

    def forward_image(self):
        if self.current_index < len(self.files) - 1:
            self.current_index += 1
            self.load_image()

    def fast_forward(self):
        for i in range(self.current_index, len(self.files)):
            base_name, _ = os.path.splitext(self.files[i])
            if not os.path.exists(os.path.join(self.output_folder, base_name + ".jpg")):
                self.current_index = i
                break
        self.load_image()

    def resizeEvent(self, event):
        self.viewer.fitInView()
        super().resizeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = ImageDescriber()
    ex.show()
    sys.exit(app.exec_())