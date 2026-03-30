import os
import requests
from PIL import Image
import numpy as np

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

DEFAULT_MODEL_URL = "https://github.com/kuronime/manga-panel-detection/releases/download/v1.0.0/manga-panel-det-v8n.pt"
MODEL_FILENAME = "model.pt" # Default to user provided model
FALLBACK_FILENAME = "manga-panel-det-v8n.pt"

class YoloDetector:
    def __init__(self, model_path=None):
        if YOLO is None:
            raise ImportError("The 'ultralytics' package is required for YOLO detection. Please run 'pip install ultralytics'.")
        
        self.model_path = model_path or MODEL_FILENAME
        
        # If user specifies 'manga109' but we don't have it, download fallback
        if self.model_path == "manga109":
             self.model_path = FALLBACK_FILENAME

        # Auto-download fallback if it's explicitly requested or missing
        if not os.path.exists(self.model_path) and self.model_path == FALLBACK_FILENAME:
            print(f"Downloading pre-trained manga panel detection model to {self.model_path}...")
            response = requests.get(DEFAULT_MODEL_URL, stream=True)
            if response.status_code == 200:
                with open(self.model_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                print("Download complete.")
            else:
                raise Exception(f"Failed to download model from {DEFAULT_MODEL_URL}. Status code: {response.status_code}")
        
        if not os.path.exists(self.model_path):
             raise FileNotFoundError(f"Model file {self.model_path} not found. Please provide {MODEL_FILENAME} or use 'manga109'.")
             
        self.model = YOLO(self.model_path)

    def detect(self, img_pil, conf=0.40):
        """
        Detect panels in a PIL image using YOLOv8.
        Returns: List of (x, y, w, h) bounding boxes.
        """
        results = self.model.predict(img_pil, conf=conf, verbose=False)
        boxes = []
        
        if results and len(results) > 0:
            # ultralytics results[0].boxes contains detections
            for result_box in results[0].boxes:
                # result_box.xyxy is [x1, y1, x2, y2]
                xyxy = result_box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = xyxy
                w = x2 - x1
                h = y2 - y1
                boxes.append((int(x1), int(y1), int(w), int(h)))
        
        return boxes
