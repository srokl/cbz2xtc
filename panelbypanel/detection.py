from PIL import Image
try:
    from .yolo_detection import YoloDetector
except (ImportError, ValueError):
    from yolo_detection import YoloDetector

# Global cache for detector instances
_DETECTOR_CACHE = {}

def detect_panels(img_pil, is_rtl=False, method="opencv", model_path=None, conf=0.40):
    """
    Detect panels in a PIL image.
    method: "opencv" (contour based) or "yolo" (YOLOv8 based).
    is_rtl: If True, sort panels for RTL reading.
    Returns: List of (x, y, w, h) bounding boxes.
    """
    if method == "yolo" or model_path:
        # Use singleton/cached detector instance
        global _DETECTOR_CACHE
        cache_key = model_path or "default"
        if cache_key not in _DETECTOR_CACHE:
            _DETECTOR_CACHE[cache_key] = YoloDetector(model_path)
        
        detector = _DETECTOR_CACHE[cache_key]
        panel_boxes = detector.detect(img_pil, conf=conf)
    else:
        # Backward compatibility / Default OpenCV logic
        import cv2
        import numpy as np
        
        # Convert PIL Image to OpenCV format (BGR)
        image = cv2.cvtColor(np.array(img_pil.convert('RGB')), cv2.COLOR_RGB2BGR)

        height, width = image.shape[:2]
        max_dim = 4000
        scale = max_dim / max(height, width)
        image_resized = cv2.resize(image, (int(width * scale), int(height * scale)))

        gray = cv2.cvtColor(image_resized, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
        morphed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        inverted = cv2.bitwise_not(morphed)
        contours, _ = cv2.findContours(inverted, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        panel_boxes = []
        for cnt in contours:
            x_r, y_r, w_r, h_r = cv2.boundingRect(cnt)
            if w_r * h_r > 10000:
                # Rescale back to original image size
                x = int(x_r / scale)
                y = int(y_r / scale)
                w = int(w_r / scale)
                h = int(h_r / scale)
                # Ensure it fits in the original image
                x = max(0, x)
                y = max(0, y)
                w = min(w, width - x)
                h = min(h, height - y)
                panel_boxes.append((x, y, w, h))

    # 3. Post-process: filter out overly small or thin detections that are likely noise/shing
    filtered_boxes = []
    min_area = 20000 # Increased for larger images
    min_dim = 60    # Increased
    max_aspect_ratio = 8.0 # Filter out slivers
    
    for x, y, w, h in panel_boxes:
        area = w * h
        aspect_ratio = max(w/h, h/w) if w > 0 and h > 0 else 0
        if (area > min_area) and (w > min_dim) and (h > min_dim) and (aspect_ratio < max_aspect_ratio):
            filtered_boxes.append((x, y, w, h))

    def sort_panels(boxes):
        if not boxes:
            return []
            
        # Use a dynamic tolerance based on the average height of panels (or image height)
        # 10% of the average panel height is usually a good row-grouping threshold
        avg_panel_h = sum(b[3] for b in boxes) / len(boxes)
        row_tolerance = avg_panel_h * 0.5 # 50% of avg panel height should safely group rows
        
        # Sort by Y first to group into rows
        boxes = sorted(boxes, key=lambda b: b[1])
        rows, current_row = [], []
        for box in boxes:
            if not current_row or abs(box[1] - current_row[0][1]) < row_tolerance:
                current_row.append(box)
            else:
                if is_rtl:
                    rows.append(sorted(current_row, key=lambda b: b[0], reverse=True))
                else:
                    rows.append(sorted(current_row, key=lambda b: b[0]))
                current_row = [box]
        if current_row:
            if is_rtl:
                rows.append(sorted(current_row, key=lambda b: b[0], reverse=True))
            else:
                rows.append(sorted(current_row, key=lambda b: b[0]))
        return [box for row in rows for box in row]

    return sort_panels(filtered_boxes)

def extract_panels(img_pil, is_rtl=False, method="opencv", model_path=None, conf=0.40):
    """
    Detect AND crop panels from a PIL image.
    Returns: List of PIL Image objects (the cropped panels).
    """
    boxes = detect_panels(img_pil, is_rtl, method=method, model_path=model_path, conf=conf)
    return [img_pil.crop((x, y, x + w, y + h)) for (x, y, w, h) in boxes]
