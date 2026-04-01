## Installation

Install the required Python libraries:
```bash
pip install pillow numpy opencv-python ultralytics requests pymupdf numba
```

## Usage

Panel extraction is triggered by adding the `--panel` flag to your `cbz2xtc.py` command.

### Method 1: OpenCV (Default)
Fast contour-based detection using traditional computer vision. No extra model files are needed.
```bash
./cbz2xtc.py --2bit --dither zhoufang --downscale lanczos --panel --rtl
```

### Method 2: YOLO (High Accuracy)
AI-based detection using YOLO. This requires a model file.
```bash
./cbz2xtc.py --2bit --dither zhoufang --downscale lanczos --panel --panel-model manga_panel_detector_fp32.pt --rtl
```

## Advanced Options

- **`--panel-conf <0.0-1.0>`**: Set the confidence threshold (default: `0.40`). Use `0.6` or `0.7` to reduce duplicate detections of the same panel.
- **`--rtl`**: Enable Right-to-Left panel sorting (standard for Japanese manga).

## Finding Models

You can find pre-trained YOLO manga panel detection models on **[Hugging Face](https://huggingface.co/models?search=manga+panel+detection)**.
Search for `manga panel detection` and download the `.pt` file (YOLOv8 format).
