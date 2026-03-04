import io
import os
import sys
import time
import yaml
import html
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
    finished_file_signal = pyqtSignal(str)
    preview_ready_signal = pyqtSignal(str, bytes, str, str, str, str) 

    def __init__(self):
        super().__init__()
        self.task_queue = queue.Queue()
        self.running = True
        self.in_progress = set()

    def add_task(self, task):
        self.in_progress.add(os.path.abspath(task.file_path))
        self.task_queue.put(task)

    def run(self):
        while self.running:
            try:
                task = self.task_queue.get(timeout=1)
                self.process_image(task)
                path = os.path.abspath(task.file_path)
                if path in self.in_progress:
                    self.in_progress.remove(path)
                self.finished_file_signal.emit(path)
            except queue.Empty:
                continue

    def process_image(self, task):
        with Image.open(task.file_path) as opened:
            img = opened.copy()
        img = ImageOps.exif_transpose(img)

        if task.rotation != 0:
            img = img.rotate(task.rotation, expand=True)

        # Prepare EXIF for files
        exif_dict = {"0th": {}, "Exif": {}, "1st": {}, "thumbnail": None, "GPS": {}}
        formatted_date = f"{task.y if task.y else '1900'}:{task.m.zfill(2) if task.m else '01'}:{task.d.zfill(2) if task.d else '01'}"
        exif_full_date = f"{formatted_date} 12:00:00"
        
        exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_full_date
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_full_date

        if task.description:
            exif_dict['0th'][piexif.ImageIFD.ImageDescription] = task.description.encode('utf-8')

        exif_bytes = piexif.dump(exif_dict)

        # Send preview to UI
        new_webp_path = os.path.join(task.folder_path, task.new_webp_name)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        self.preview_ready_signal.emit(
            os.path.abspath(new_webp_path),
            buf.getvalue(),
            task.y or "",
            task.m or "",
            task.d or "",
            task.description or "",
        )

        # Clean up existing files in processed folder
        if os.path.exists(task.output_folder):
            for existing_file in os.listdir(task.output_folder):
                name_part, _ = os.path.splitext(existing_file)
                if name_part == task.base_name:
                    try:
                        os.remove(os.path.join(task.output_folder, existing_file))
                    except: pass

        # 1. SAVE LOSSLESS TO ROOT
        img.save(new_webp_path, "WEBP", lossless=True, method=6, exif=exif_bytes)
        self.log_signal.emit(f"SAVED (Lossless): {task.new_webp_name}")

        # 2. SAVE LOSSY TO PROCESSED/
        lossy_path = os.path.join(task.output_folder, task.new_webp_name)
        img.convert("RGB").save(lossy_path, "WEBP", quality=90, method=6, exif=exif_bytes)
        self.log_signal.emit(f"SAVED (Lossy): {task.new_webp_name} to processed/")

        # 3. SAVE XMP SIDECAR TO PROCESSED/ ONLY
        # Standard XMP format that Immich/Lightroom can read
        xmp_path = lossy_path + ".xmp"
        # Convert date to ISO format
        iso_date = formatted_date.replace(":", "-")
        
        # Defaulting to 12:00:00Z (UTC) to prevent local-time shifting.
        # Escape special characters like & < > "
        safe_desc = html.escape(task.description if task.description else "")
        iso_date = formatted_date.replace(":", "-")
        
        xmp_template = f"""<?xml version="1.0" encoding="UTF-8"?>
        <x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.6.0">
         <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
          <rdf:Description rdf:about=""
            xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:exif="http://ns.adobe.com/exif/1.0/"
            xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
            xmlns:xmp="http://ns.adobe.com/xap/1.0/"
            xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
            xmlns:digiKam="http://www.digikam.org/ns/1.0/">
           
           <dc:title>
            <rdf:Alt>
             <rdf:li xml:lang="x-default">{safe_desc}</rdf:li>
            </rdf:Alt>
           </dc:title>
           <dc:description>
            <rdf:Alt>
             <rdf:li xml:lang="x-default">{safe_desc}</rdf:li>
            </rdf:Alt>
           </dc:description>
           <tiff:ImageDescription>{safe_desc}</tiff:ImageDescription>
           
           <exif:DateTimeOriginal>{iso_date}T12:00:00</exif:DateTimeOriginal>
           <xmp:CreateDate>{iso_date}T12:00:00</xmp:CreateDate>
           <xmp:ModifyDate>{iso_date}T12:00:00</xmp:ModifyDate>
           
           <tiff:Orientation>1</tiff:Orientation>
           <xmp:Rating>0</xmp:Rating>
           
           <dc:subject>
            <rdf:Bag/>
           </dc:subject>
           <lr:HierarchicalSubject>
            <rdf:Bag/>
           </lr:HierarchicalSubject>
           <digiKam:TagsList>
            <rdf:Bag/>
           </digiKam:TagsList>
           
          </rdf:Description>
         </rdf:RDF>
        </x:xmpmeta>"""

        try:
            with open(xmp_path, "w", encoding="utf-8") as xf:
                xf.write(xmp_template)
            if self.receivers(self.log_signal) > 0:
                self.log_signal.emit(f"SAVED Sidecar: {os.path.basename(xmp_path)}")
        except Exception as e:
            self.log_signal.emit(f"XMP Error: {e}")

        img.close()

        # Delete source logic
        if os.path.abspath(task.file_path) != os.path.abspath(new_webp_path):
            for attempt in range(3):
                try:
                    os.remove(task.file_path)
                    break
                except PermissionError:
                    if attempt < 2: time.sleep(0.25)
                    
                    
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
        self.processor.preview_ready_signal.connect(self.on_preview_ready)
        self.processor.start()
        # In-memory preview: path -> (image_bytes, y, m, d, description)
        self.preview_cache = {}

        self.initUI()
        if self.folder_path and os.path.exists(self.folder_path):
            self.scan_folder(self.folder_path)
            
    def on_preview_ready(self, webp_path, image_bytes, y, m, d, desc):
        self.preview_cache[os.path.abspath(webp_path)] = (image_bytes, y, m, d, desc or "")

    def on_file_finished(self, path):
        # If the user is currently looking at the file that just finished
        current_file = os.path.abspath(os.path.join(self.folder_path, self.files[self.current_index]))
        if path == current_file:
            self.load_image()

    def initUI(self):
        self.setWindowTitle('Photo Metadata Tagger')
        self.setGeometry(100, 100, 1000, 950)
        self.main_layout = QVBoxLayout()

        # Folder selection
        self.top_layout = QHBoxLayout()
        self.path_label = QLabel(self.folder_path if self.folder_path else "No folder selected")
        self.path_label.setStyleSheet("color: #666; font-style: italic;")
        
        # New Reload & FF Button
        self.reload_and_ff_btn = QPushButton("↻ Reload && FF")
        self.reload_and_ff_btn.clicked.connect(self.reload_and_ff)
        
        self.browse_btn = QPushButton("Browse Folder")
        self.browse_btn.clicked.connect(self.select_folder)
        
        self.top_layout.addWidget(self.path_label, 1)
        self.top_layout.addWidget(self.reload_and_ff_btn) # Added to the left of browse
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
        self.next_btn = QPushButton("Process && Next →")
        
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

    def reload_and_ff(self):
        """Rescans the current folder and fast forwards to the first unprocessed image."""
        if self.folder_path and os.path.exists(self.folder_path):
            self.update_console("Reloading folder...")
            self.scan_folder(self.folder_path)
            self.fast_forward()

    def rotate_image(self, angle):
        """Rotate visually in the viewer and track the total angle for PIL."""
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
        # Pass the current folder_path as the starting directory
        p = QFileDialog.getExistingDirectory(self, "Select Folder", self.folder_path)
        if p: 
            # Reset index before scanning to ensure we start at the first image
            self.current_index = 0
            self.scan_folder(p)

    def scan_folder(self, p):
        self.folder_path = p
        self.preview_cache.clear()
        self.path_label.setText(p)
        self.save_last_path()
        
        self.output_folder = os.path.join(p, "processed")
        if not os.path.exists(self.output_folder): 
            os.makedirs(self.output_folder)
        
        # Ensure we only grab actual image files
        valid = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png', '.webp')
        self.files = sorted([f for f in os.listdir(p) if f.lower().endswith(valid)])
        
        if self.files:
            self.update_console(f"Found {len(self.files)} images.")
            self.load_image()
        else:
            self.update_console("No valid images found in selected folder.")
            self.viewer.setPhoto(None)
            self.progress_label.setText("No images found.")
            self.file_info_label.setText("")

    def load_image(self):
        self.current_rotation = 0 
        self.viewer.resetTransform() 
        
        if not self.files or self.current_index >= len(self.files):
            self.viewer.setPhoto(None)
            self.progress_label.setText("Done!")
            return
        
        filename = self.files[self.current_index]
        img_path = os.path.abspath(os.path.join(self.folder_path, filename))

        self.progress_label.setText(f"Image {self.current_index + 1} of {len(self.files)}")
        self.file_info_label.setText(f"File: {filename}")
        self.desc_input.clear()

        # Use in-memory preview if we have it (same image/metadata as sent to save)
        if img_path in self.preview_cache:
            image_bytes, y, m, d, desc = self.preview_cache[img_path]
            pixmap = QPixmap()
            pixmap.loadFromData(image_bytes)
            self.viewer.setPhoto(pixmap)
            if y:
                self.year_input.setText(y)
            if m:
                self.month_input.setText(m)
            if d:
                self.day_input.setText(d)
            if desc:
                self.desc_input.setText(desc)
            self.desc_input.setFocus()
            return

        try:
            with open(img_path, 'rb') as f:
                data = f.read()
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                self.viewer.setPhoto(pixmap)
        except Exception as e:
            self.viewer.setPhoto(None)
            self.update_console(f"Error loading {filename}: {e}")

        y, m, d, desc = get_existing_metadata(img_path, self.output_folder)
        if y:
            self.year_input.setText(y)
        if m:
            self.month_input.setText(m)
        if d:
            self.day_input.setText(d)
        if desc:
            self.desc_input.setText(desc)

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
            rotation=self.current_rotation
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
        valid_exts = ('.jpg', '.jpeg', '.tiff', '.bmp', '.png', '.webp')
        
        for i in range(0, len(self.files)): # Check from start after a reload
            filename = self.files[i]
            base_name, _ = os.path.splitext(filename)
            
            already_processed = False
            for ext in valid_exts:
                if os.path.exists(os.path.join(self.output_folder, base_name + ext)):
                    already_processed = True
                    break
            
            if already_processed:
                continue
                
            self.current_index = i
            break
        else:
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