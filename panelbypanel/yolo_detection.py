import os
from PIL import Image
import numpy as np

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

MODEL_FILENAME = os.path.join(os.path.dirname(__file__), "best.pt")

# Global cache to reuse loaded YOLO models across multiple instances/threads
_MODEL_CACHE = {}

class YoloDetector:
    def __init__(self, model_path=None):
        if YOLO is None:
            raise ImportError("The 'ultralytics' package is required for YOLO detection. Please run 'pip install ultralytics'.")
        
        self.model_path = model_path or MODEL_FILENAME
        
        if not os.path.exists(self.model_path):
             raise FileNotFoundError(f"Model file {self.model_path} not found. Please provide {MODEL_FILENAME} in the panelbypanel folder.")
             
        # Use singleton/cached model instance
        global _MODEL_CACHE
        if self.model_path not in _MODEL_CACHE:
            print(f"Loading YOLO model from {self.model_path}...") # Inform user of first-time load
            _MODEL_CACHE[self.model_path] = YOLO(self.model_path)
            
        self.model = _MODEL_CACHE[self.model_path]

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
