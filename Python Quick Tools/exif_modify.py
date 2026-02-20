import os
import sys
import yaml
from pathlib import Path
from platformdirs import user_config_dir
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog, 
                             QGraphicsView, QGraphicsScene, QFrame, QPlainTextEdit)
from PyQt5.QtGui import QPixmap, QFont
from PyQt5.QtCore import Qt, QRectF, QThread, pyqtSignal
from PIL import Image, ImageOps
import piexif
import queue

# --- NEW UTILITY FUNCTION FOR HEADLESS & GUI ---
def get_existing_metadata(file_path, output_folder):
    """
    Looks for EXIF data in the source file or a previously processed version.
    Returns (y, m, d, description)
    """
    y, m, d, desc_str = "", "", "", ""
    exif_data = None
    filename = os.path.basename(file_path)
    
    # Try source file
    try:
        with Image.open(file_path) as img:
            exif_data = img.info.get("exif")
    except: pass

    # Try processed folder if source has none
    if not exif_data:
        base_name, _ = os.path.splitext(filename)
        for ext in ['.webp', '.jpg', '.jpeg']:
            p_path = os.path.join(output_folder, base_name + ext)
            if os.path.exists(p_path):
                try:
                    with Image.open(p_path) as p_img:
                        exif_data = p_img.info.get("exif")
                        if exif_data: break
                except: pass

    if exif_data:
        exif = piexif.load(exif_data)
        # Extract Date
        dt = exif.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
        if dt:
            try:
                date_part = dt.decode().split(' ')[0]
                y, m, d = date_part.split(':')
            except: pass
        
        # Extract Description
        raw_desc = exif.get("0th", {}).get(piexif.ImageIFD.ImageDescription)
        if raw_desc:
            try:
                desc_str = raw_desc.decode('utf-8')
            except: pass
            
    return y, m, d, desc_str

class ImageTask:
    def __init__(self, file_path, output_folder, folder_path, y, m, d, description, rotation):
        self.file_path = file_path
        self.output_folder = output_folder
        self.folder_path = folder_path
        self.base_name, self.ext = os.path.splitext(os.path.basename(file_path))
        self.y, self.m, self.d = y, m, d
        self.description = description
        self.rotation = rotation
        self.new_webp_name = self.base_name + ".webp"

class ProcessorThread(QThread):
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.task_queue = queue.Queue()
        self.running = True

    def add_task(self, task):
        self.task_queue.put(task)

    def run(self):
        while self.running:
            try:
                task = self.task_queue.get(timeout=1)
                self.process_image(task)
            except queue.Empty:
                continue

    def process_image(self, task):
        img = Image.open(task.file_path)
        img = ImageOps.exif_transpose(img)
        
        if task.rotation != 0:
            img = img.rotate(task.rotation, expand=True)
        
        exif_dict = {"0th": {}, "Exif": {}, "1st": {}, "thumbnail": None, "GPS": {}}
        exif_date = f"{task.y if task.y else '1900'}:{task.m.zfill(2) if task.m else '01'}:{task.d.zfill(2) if task.d else '01'} 12:00:00"
        exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_date
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_date
        
        if task.description:
            exif_dict['0th'][piexif.ImageIFD.ImageDescription] = task.description.encode('utf-8')

        exif_bytes = piexif.dump(exif_dict)
        
        if os.path.exists(task.output_folder):
            for existing_file in os.listdir(task.output_folder):
                name_part, _ = os.path.splitext(existing_file)
                if name_part == task.base_name:
                    try:
                        os.remove(os.path.join(task.output_folder, existing_file))
                        self.log_signal.emit(f"DELETED existing: {existing_file}")
                    except: pass
                    
        new_webp_path = os.path.join(task.folder_path, task.new_webp_name)
        img.save(new_webp_path, "WEBP", lossless=True, method=6, exif=exif_bytes)
        self.log_signal.emit(f"SAVED (Lossless): {task.new_webp_name}")

        lossy_path = os.path.join(task.output_folder, task.new_webp_name)
        img.convert("RGB").save(lossy_path, "WEBP", quality=90, method=6, exif=exif_bytes)
        self.log_signal.emit(f"SAVED (Lossy): {task.new_webp_name} to processed/")

        img.close()
        if os.path.abspath(task.file_path) != os.path.abspath(new_webp_path):
            os.remove(task.file_path)


class PhotoViewer(QGraphicsView):
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
        self.setBackgroundBrush(Qt.white)
        self.setFrameShape(QFrame.NoFrame)

    def hasPhoto(self): return not self._empty

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
        self.setSceneRect(QRectF(self._photo.pixmap().rect()))
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
                factor = min(viewrect.width() / (scenerect.width() or 1),
                             viewrect.height() / (scenerect.height() or 1))
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
            if self._zoom > 0: self.scale(factor, factor)
            else: self._zoom = 0; self.fitInView()

class ImageDescriber(QWidget):
    def __init__(self):
        super().__init__()
        self.config_dir = Path(user_config_dir("PythonTools", "meta_data_modify"))
        self.config_file = self.config_dir / "persistence.yaml"
        self.folder_path = self.load_last_path()
        self.files = []
        self.current_index = 0
        self.current_rotation = 0 # Track rotation for PIL processing

        self.processor = ProcessorThread()
        self.processor.log_signal.connect(self.update_console)
        self.processor.start()

        self.initUI()
        if self.folder_path and os.path.exists(self.folder_path):
            self.scan_folder(self.folder_path)

    def initUI(self):
        self.setWindowTitle('Photo Metadata Tagger')
        self.setGeometry(100, 100, 1000, 950)
        self.main_layout = QVBoxLayout()

        # Folder selection
        self.top_layout = QHBoxLayout()
        self.path_label = QLabel(self.folder_path if self.folder_path else "No folder selected")
        self.path_label.setStyleSheet("color: #666; font-style: italic;")
        self.browse_btn = QPushButton("Browse Folder")
        self.browse_btn.clicked.connect(self.select_folder)
        self.top_layout.addWidget(self.path_label, 1)
        self.top_layout.addWidget(self.browse_btn)
        self.main_layout.addLayout(self.top_layout)

        self.progress_label = QLabel("Select folder...")
        self.main_layout.addWidget(self.progress_label)

        # Viewer
        self.viewer = PhotoViewer(self)
        self.main_layout.addWidget(self.viewer, 1)

        self.file_info_label = QLabel("")
        self.file_info_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.file_info_label)

        # Inputs
        date_group = QHBoxLayout()
        self.year_input = QLineEdit(); self.year_input.setPlaceholderText("YYYY")
        self.month_input = QLineEdit(); self.month_input.setPlaceholderText("MM")
        self.day_input = QLineEdit(); self.day_input.setPlaceholderText("DD")
        for w in [self.year_input, self.month_input, self.day_input]:
            w.setFixedWidth(80)
            date_group.addWidget(w)
        
        # Rotation Buttons
        self.rot_left_btn = QPushButton("⟲ Rotate Left")
        self.rot_right_btn = QPushButton("⟳ Rotate Right")
        self.rot_left_btn.setFixedWidth(120)
        self.rot_right_btn.setFixedWidth(120)
        self.rot_left_btn.clicked.connect(lambda: self.rotate_image(90))
        self.rot_right_btn.clicked.connect(lambda: self.rotate_image(-90))
        
        date_group.addSpacing(20)
        date_group.addWidget(self.rot_left_btn)
        date_group.addWidget(self.rot_right_btn)
        
        date_group.addStretch()
        self.main_layout.addLayout(date_group)

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Description...")
        self.main_layout.addWidget(self.desc_input)

        # Navigation
        nav = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.forward_btn = QPushButton("Forward →")
        self.ff_btn = QPushButton("Fast Forward ⏩")
        self.next_btn = QPushButton("Process & Next →")
        
        self.ff_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        self.next_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        
        for btn in [self.back_btn, self.forward_btn, self.ff_btn, self.next_btn]:
            btn.setFixedHeight(45)
            nav.addWidget(btn)
        
        self.back_btn.clicked.connect(self.prev_image)
        self.forward_btn.clicked.connect(self.forward_image)
        self.ff_btn.clicked.connect(self.fast_forward)
        self.next_btn.clicked.connect(self.process_and_next)
        self.main_layout.addLayout(nav)

        # Console
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFixedHeight(80) 
        self.console.setFont(QFont("Consolas", 9))
        self.console.setStyleSheet("background-color: #2b2b2b; color: #a9b7c6; border: 1px solid #333;")
        self.main_layout.addWidget(self.console)

        self.setLayout(self.main_layout)
        self.desc_input.returnPressed.connect(self.process_and_next)

    def rotate_image(self, angle):
        """Rotate visually in the viewer and track the total angle for PIL."""
        # angle is positive for CCW (Left) and negative for CW (Right)
        # PIL also uses positive CCW, so this maps perfectly.
        self.current_rotation = (self.current_rotation + angle) % 360
        self.viewer.rotate(-angle) # QGraphicsView.rotate uses Clockwise as positive

    def update_console(self, message):
        self.console.appendPlainText(message)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def load_last_path(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    return yaml.safe_load(f).get("last_path", "")
        except: return ""

    def save_last_path(self):
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w') as f:
                yaml.dump({"last_path": self.folder_path}, f)
        except: pass

    def select_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Select Folder", self.folder_path)
        if p: self.scan_folder(p)

    def scan_folder(self, p):
        self.folder_path = p
        self.path_label.setText(p)
        self.save_last_path()
        self.output_folder = os.path.join(p, "processed")
        if not os.path.exists(self.output_folder): os.makedirs(self.output_folder)
        valid = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png', '.webp')
        self.files = sorted([f for f in os.listdir(p) if f.lower().endswith(valid)])
        self.current_index = 0
        self.load_image()

    def load_image(self):
        self.current_rotation = 0 
        self.viewer.resetTransform() 
        
        if not self.files or self.current_index >= len(self.files):
            self.viewer.setPhoto(None)
            self.progress_label.setText("Done!")
            self.file_info_label.setText("")
            return
        
        filename = self.files[self.current_index]
        self.progress_label.setText(f"Image {self.current_index + 1} of {len(self.files)}")
        self.file_info_label.setText(f"File: {filename}")
        self.desc_input.clear()
    
        img_path = os.path.join(self.folder_path, filename)
        
        # USE THE NEW UTILITY FUNCTION
        y, m, d, desc = get_existing_metadata(img_path, self.output_folder)
        
        if y: self.year_input.setText(y)
        if m: self.month_input.setText(m)
        if d: self.day_input.setText(d)
        if desc: self.desc_input.setText(desc)
        
        self.viewer.setPhoto(QPixmap(img_path))
        self.desc_input.setFocus()

    def process_and_next(self):
        if self.current_index >= len(self.files): return
        filename = self.files[self.current_index]
        
        task = ImageTask(
            file_path=os.path.join(self.folder_path, filename),
            output_folder=self.output_folder,
            folder_path=self.folder_path,
            y=self.year_input.text(),
            m=self.month_input.text(),
            d=self.day_input.text(),
            description=self.desc_input.text().strip(),
            rotation=self.current_rotation # Pass the rotation to the worker
        )

        self.files[self.current_index] = task.new_webp_name
        self.processor.add_task(task)
        self.current_index += 1
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
            # Define extensions to check for in the output folder
            valid_exts = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png', '.webp')
            
            for i in range(self.current_index, len(self.files)):
                filename = self.files[i]
                base_name, _ = os.path.splitext(filename)
                
                # Check if any version of this file exists in the processed folder
                already_processed = False
                for ext in valid_exts:
                    if os.path.exists(os.path.join(self.output_folder, base_name + ext)):
                        already_processed = True
                        break
                
                if already_processed:
                    continue
                    
                # If we find a file that hasn't been processed yet, stop here
                self.current_index = i
                break
            else:
                # If all remaining files were processed, go to the end
                self.current_index = len(self.files)
                
            self.load_image()

    def resizeEvent(self, event):
        self.viewer.fitInView()
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.processor.running = False
        self.processor.wait()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = ImageDescriber()
    ex.show()
    sys.exit(app.exec_())