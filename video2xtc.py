#!/usr/bin/env python3
"""
video2xtc - Convert Video files to XTC/XTCH format for XTEink X4
Extracts frames at 1 FPS and converts them.

Usage:
    video2xtc <video_file>     # Process video
    video2xtc <video> --2bit   # Use 2-bit grayscale
    video2xtc <video> --fps 2  # Extract 2 frames per second
"""

import os
import sys
import shutil
import subprocess
import struct
import hashlib
import time
import numpy as np
try:
    from numba import njit
except ImportError:
    def njit(func): return func
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration
DEVICE_DIMENSIONS = {
    'X4': (480, 800),
    'X3': (528, 792)
}

TARGET_WIDTH, TARGET_HEIGHT = DEVICE_DIMENSIONS['X4']

# Global configuration (defaults)
XTC_MODE = "1bit"
DITHER_ALGO = "stucki"
GAMMA_VALUE = 1.0
INVERT_COLORS = False
FPS_VALUE = 1.0

DITHER_MAP = {
    'floyd': Image.Dither.FLOYDSTEINBERG,
    'ordered': Image.Dither.ORDERED,
    'rasterize': Image.Dither.RASTERIZE,
    'none': Image.Dither.NONE,
    'atkinson': 'atkinson',
    'stucki': 'stucki',
    'ostromoukhov': 'ostromoukhov'
}

@njit
def _zhoufang_loop(data, w, h, stride, is_2bit):
    for y in range(h):
        row_start = y * stride
        for x in range(1, w + 1):
            idx = row_start + x
            old_val = data[idx]
            if is_2bit:
                if old_val < 42: new_val = 0
                elif old_val < 127: new_val = 85
                elif old_val < 212: new_val = 170
                else: new_val = 255
            else:
                new_val = 0 if old_val < 128 else 255
            data[idx] = new_val
            err = old_val - new_val
            if err != 0:
                e = err / 103
                if x + 1 < w: data[idx + 1] += int(e * 16)
                if x + 2 < w: data[idx + 2] += int(e * 9)
                idx_n = idx + stride
                if idx_n < len(data):
                    if x - 2 > 0: data[idx_n - 2] += int(e * 5)
                    if x - 1 > 0: data[idx_n - 1] += int(e * 11)
                    data[idx_n] += int(e * 16)
                    if x + 1 < w: data[idx_n + 1] += int(e * 11)
                    if x + 2 < w: data[idx_n + 2] += int(e * 5)
                idx_n2 = idx + (stride * 2)
                if idx_n2 < len(data):
                    if x - 2 > 0: data[idx_n2 - 2] += int(e * 1)
                    if x - 1 > 0: data[idx_n2 - 1] += int(e * 5)
                    data[idx_n2] += int(e * 9)
                    if x + 1 < w: data[idx_n2 + 1] += int(e * 5)
                    if x + 2 < w: data[idx_n2 + 2] += int(e * 3)

def dither_zhoufang(img, levels):
    w, h = img.size
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    data = buff.flatten()
    is_2bit = (len(levels) > 2)
    _zhoufang_loop(data, w, h, stride, is_2bit)
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr, 'L')

@njit
def _ostromoukhov_loop(data, w, h, stride, is_2bit):
    for y in range(h):
        row_start = y * stride
        for x in range(1, w + 1):
            idx = row_start + x
            old_val = data[idx]
            if is_2bit:
                if old_val < 42: new_val = 0
                elif old_val < 127: new_val = 85
                elif old_val < 212: new_val = 170
                else: new_val = 255
            else:
                new_val = 0 if old_val < 128 else 255
            data[idx] = new_val
            err = old_val - new_val
            if err != 0:
                # Variable coefficients based on input intensity
                # Simplified 3-point piecewise linear interpolation
                # 0/255: [0.7, 0.2, 0.1]
                # 128:   [0.3, 0.4, 0.3]
                
                v = max(0, min(255, old_val))
                if v <= 128:
                    t = v / 128.0
                    d1 = 0.7 * (1 - t) + 0.3 * t
                    d2 = 0.2 * (1 - t) + 0.4 * t
                    d3 = 0.1 * (1 - t) + 0.3 * t
                else:
                    t = (v - 128) / 127.0
                    d1 = 0.3 * (1 - t) + 0.7 * t
                    d2 = 0.4 * (1 - t) + 0.2 * t
                    d3 = 0.3 * (1 - t) + 0.1 * t
                
                # Distribute error
                # d1: Right (x+1)
                # d2: Down-Left (x-1, y+1)
                # d3: Down (x, y+1)
                
                if x + 1 < w: data[idx + 1] += int(err * d1)
                
                idx_n = idx + stride
                if idx_n < len(data):
                    if x - 1 > 0: data[idx_n - 1] += int(err * d2)
                    data[idx_n] += int(err * d3)

def dither_ostromoukhov(img, levels):
    w, h = img.size
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    data = buff.flatten()
    is_2bit = (len(levels) > 2)
    _ostromoukhov_loop(data, w, h, stride, is_2bit)
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr, 'L')

@njit
def _stucki_loop(data, w, h, stride, is_2bit):
    for y in range(h):
        row_start = y * stride
        for x in range(1, w + 1):
            idx = row_start + x
            old_val = data[idx]
            if is_2bit:
                if old_val < 42: new_val = 0
                elif old_val < 127: new_val = 85
                elif old_val < 212: new_val = 170
                else: new_val = 255
            else:
                new_val = 0 if old_val < 128 else 255
            data[idx] = new_val
            err = old_val - new_val
            if err != 0:
                # Row 1
                if x + 1 < w: data[idx + 1] += (err * 8) // 42
                if x + 2 < w: data[idx + 2] += (err * 4) // 42
                # Row 2
                idx_n = idx + stride
                if idx_n < len(data):
                    if x - 2 > 0: data[idx_n - 2] += (err * 2) // 42
                    if x - 1 > 0: data[idx_n - 1] += (err * 4) // 42
                    data[idx_n] += (err * 8) // 42
                    if x + 1 < w: data[idx_n + 1] += (err * 4) // 42
                    if x + 2 < w: data[idx_n + 2] += (err * 2) // 42
                # Row 3
                idx_n2 = idx + (stride * 2)
                if idx_n2 < len(data):
                    if x - 2 > 0: data[idx_n2 - 2] += (err * 1) // 42
                    if x - 1 > 0: data[idx_n2 - 1] += (err * 2) // 42
                    data[idx_n2] += (err * 4) // 42
                    if x + 1 < w: data[idx_n2 + 1] += (err * 2) // 42
                    if x + 2 < w: data[idx_n2 + 2] += (err * 1) // 42

def dither_stucki(img, levels):
    w, h = img.size
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    data = buff.flatten()
    is_2bit = (len(levels) > 2)
    _stucki_loop(data, w, h, stride, is_2bit)
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr, 'L')

@njit
def _atkinson_loop(data, w, h, stride, is_2bit):
    for y in range(h):
        row_start = y * stride
        for x in range(1, w + 1):
            idx = row_start + x
            old_val = data[idx]
            if is_2bit:
                if old_val < 42: new_val = 0
                elif old_val < 127: new_val = 85
                elif old_val < 212: new_val = 170
                else: new_val = 255
            else:
                new_val = 0 if old_val < 128 else 255
            data[idx] = new_val
            err = old_val - new_val
            if err != 0:
                err8 = err >> 3
                if err8 != 0:
                    data[idx + 1] += err8
                    data[idx + 2] += err8
                    idx_n = idx + stride
                    data[idx_n - 1] += err8
                    data[idx_n] += err8
                    data[idx_n + 1] += err8
                    data[idx_n + stride] += err8

def dither_atkinson(img, levels):
    w, h = img.size
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    data = buff.flatten()
    is_2bit = (len(levels) > 2)
    _atkinson_loop(data, w, h, stride, is_2bit)
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr, 'L')

def png_to_xtg_bytes(img: Image.Image, force_size=None, threshold=128):
    if force_size is None:
        force_size = (TARGET_WIDTH, TARGET_HEIGHT)
        
    if img.size != force_size:
        img = img.resize(force_size, Image.Resampling.BILINEAR)
    if img.mode != '1':
        img = img.convert("L").point(lambda p: 255 if p >= threshold else 0).convert("1")
    data = img.tobytes()
    md5digest = hashlib.md5(data).digest()[:8]
    data_size = len(data)
    header = struct.pack("<4sHHBBI8s", b"XTG\x00", force_size[0], force_size[1], 0, 0, data_size, md5digest)
    return header + data

def png_to_xth_bytes(img: Image.Image, force_size=None):
    if force_size is None:
        force_size = (TARGET_WIDTH, TARGET_HEIGHT)

    if img.size != force_size:
        img = img.resize(force_size, Image.Resampling.BILINEAR)
    arr = np.array(img.convert('L'))
    vals = np.zeros_like(arr, dtype=np.uint8)
    vals[arr < 42] = 3
    vals[(arr >= 42) & (arr < 127)] = 2
    vals[(arr >= 127) & (arr < 212)] = 1
    vals[arr >= 212] = 0
    vals = vals[:, ::-1].T
    p0 = (vals & 1).astype(np.uint8)
    p1 = ((vals & 2) >> 1).astype(np.uint8)
    plane0 = np.packbits(p0, axis=1).tobytes()
    plane1 = np.packbits(p1, axis=1).tobytes()
    data = plane0 + plane1
    md5digest = hashlib.md5(data).digest()[:8]
    data_size = len(data)
    header = struct.pack("<4sHHBBI8s", b"XTH\x00", force_size[0], force_size[1], 0, 0, data_size, md5digest)
    return header + data

def build_xtc_internal(png_paths, out_path, mode="1bit"):
    blobs = [None] * len(png_paths)
    print(f"  Encoding {len(png_paths)} frames ({mode})...", end=" ", flush=True)
    
    def encode_page(args):
        idx, p, mode = args
        try:
            img = Image.open(p)
            if mode == "2bit": return idx, png_to_xth_bytes(img)
            else: return idx, png_to_xtg_bytes(img)
        except Exception as e:
            return idx, None

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
        results = list(executor.map(encode_page, [(i, p, mode) for i, p in enumerate(png_paths)]))
        
    for idx, blob in results:
        if blob is None: return False
        blobs[idx] = blob

    page_count = len(blobs)
    header_size = 56
    metadata_size = 256
    chapter_count = 0
    chapters_size = 0
    index_entry_size = 16
    index_size = page_count * index_entry_size
    
    metadata_offset = header_size
    chapter_offset = metadata_offset + metadata_size
    index_offset = chapter_offset + chapters_size
    data_offset = index_offset + index_size

    index_table = bytearray()
    rel_offset = data_offset
    for blob in blobs:
        w, h = struct.unpack_from("<HH", blob, 4)
        entry = struct.pack("<Q I H H", rel_offset, len(blob), w, h)
        index_table += entry
        rel_offset += len(blob)

    magic = b"XTCH" if mode == "2bit" else b"XTC\x00"
    xtc_header = struct.pack("<4sHHBBBBIQQQQQ", magic, 1, page_count, 0, 1, 0, 0, 1, metadata_offset, index_offset, data_offset, 0, chapter_offset)
    
    metadata = bytearray(256)
    title = os.path.basename(out_path).encode('utf-8')[:127]
    metadata[0:len(title)] = title
    timestamp = int(time.time())
    struct.pack_into("<I", metadata, 240, timestamp)
    struct.pack_into("<H", metadata, 246, chapter_count)
    
    with open(out_path, "wb") as f:
        f.write(xtc_header)
        f.write(metadata)
        f.write(index_table)
        for blob in blobs:
            f.write(blob)
    print("✓")
    return True

def optimize_frame(img, output_path):
    # Resize logic for video frames
    # Always fit to screen. 
    # If Landscape (W >= H) -> Rotate -90 -> Portrait
    width, height = img.size
    if width >= height:
        img = img.rotate(-90, expand=True)
    
    img_width, img_height = img.size
    scale = min(TARGET_WIDTH / img_width, TARGET_HEIGHT / img_height)
    new_width = int(img_width * scale)
    new_height = int(img_height * scale)
    img = img.resize((new_width, new_height), Image.Resampling.BICUBIC)
    
    result = Image.new('L', (TARGET_WIDTH, TARGET_HEIGHT), color=0) # Black background for video
    x = (TARGET_WIDTH - new_width) // 2
    y = (TARGET_HEIGHT - new_height) // 2
    
    if img.mode != 'L':
        img = img.convert('L')
        
    result.paste(img, (x, y))
    
    if INVERT_COLORS: result = ImageOps.invert(result)
    if GAMMA_VALUE != 1.0:
        lut = [int(((i / 255.0) ** GAMMA_VALUE) * 255.0) for i in range(256)]
        result = result.point(lut)

    if XTC_MODE == "2bit":
        if DITHER_ALGO == 'none':
            lut = []
            for i in range(256):
                if i < 42: val = 0
                elif i < 127: val = 85
                elif i < 212: val = 170
                else: val = 255
                lut.append(val)
            result = result.point(lut)
        elif DITHER_ALGO == 'atkinson':
            result = dither_atkinson(result, levels=[0, 85, 170, 255])
        elif DITHER_ALGO == 'stucki':
            result = dither_stucki(result, levels=[0, 85, 170, 255])
        elif DITHER_ALGO == 'ostromoukhov':
            result = dither_ostromoukhov(result, levels=[0, 85, 170, 255])
        elif DITHER_ALGO == 'zhoufang':
            result = dither_zhoufang(result, levels=[0, 85, 170, 255])
        else:
            pal_img = Image.new("P", (1, 1))
            pal_img.putpalette([0,0,0, 85,85,85, 170,170,170, 255,255,255] + [0,0,0]*252)
            result_rgb = result.convert('RGB')
            result = result_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
            result = result.convert('L')
    else:
        if DITHER_ALGO == 'atkinson':
            result = dither_atkinson(result, levels=[0, 255])
            result = result.convert('1', dither=Image.Dither.NONE)
        elif DITHER_ALGO == 'stucki':
            result = dither_stucki(result, levels=[0, 255])
            result = result.convert('1', dither=Image.Dither.NONE)
        elif DITHER_ALGO == 'ostromoukhov':
            result = dither_ostromoukhov(result, levels=[0, 255])
            result = result.convert('1', dither=Image.Dither.NONE)
        elif DITHER_ALGO == 'zhoufang':
            result = dither_zhoufang(result, levels=[0, 255])
            result = result.convert('1', dither=Image.Dither.NONE)
        else:
            dither_mode = DITHER_MAP.get(DITHER_ALGO, Image.Dither.FLOYDSTEINBERG)
            result = result.convert('1', dither=dither_mode)
        result = result.convert('L')

    result.save(output_path, 'PNG')

def extract_video_frames(video_path, temp_dir):
    if not shutil.which("ffmpeg"):
        print("  ✗ Error: ffmpeg not found.")
        return None
    
    name = video_path.stem
    output_folder = temp_dir / name
    output_folder.mkdir(parents=True, exist_ok=True)
    
    print(f"  Extracting frames from {video_path.name} at {FPS_VALUE} fps...", end=" ", flush=True)
    
    cmd = [
        "ffmpeg", 
        "-i", str(video_path), 
        "-vf", f"fps={FPS_VALUE}", 
        str(output_folder / "%05d.png"),
        "-y", "-loglevel", "error"
    ]
    
    try:
        subprocess.run(cmd, check=True)
        files = sorted(output_folder.glob("*.png"))
        print(f"✓ ({len(files)} frames)")
        
        # Optimize in place
        print("  Optimizing frames...", end=" ", flush=True)
        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(optimize_frame, Image.open(f), f) for f in files]
            for _ in as_completed(futures): pass
        print("✓")
        
        return output_folder
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None

def process_file(file_path, output_dir, temp_dir, clean_temp, file_num, total_files):
    print(f"\n[{file_num}/{total_files}] Processing: {file_path.name}")
    start_time = time.time()
    
    png_folder = extract_video_frames(file_path, temp_dir)
    if not png_folder: return False
    
    ext = ".xtch" if XTC_MODE == "2bit" else ".xtc"
    output_file = output_dir / f"{file_path.stem}{ext}"
    
    png_files = sorted(png_folder.glob("*.png"))
    success = build_xtc_internal(png_files, output_file, mode=XTC_MODE)
    
    if clean_temp:
        shutil.rmtree(png_folder)
        
    elapsed = time.time() - start_time
    print(f"  ⏱  {elapsed:.1f}s")
    return success

def main():
    print("=" * 60)
    print("Video to XTC Converter for XTEink X4")
    print("=" * 60)
    
    if "--help" in sys.argv or "-h" in sys.argv:
        print("\nUsage: video2xtc <video_file> [options]")
        print("Options:")
        print("  --fps <float>    Frames per second (default 1.0)")
        print("  --2bit           Use 2-bit grayscale")
        print("  --dither <algo>  stucki (default), atkinson, ostromoukhov, zhoufang, floyd, ordered, none")
        print("  --gamma <float>  Brightness (default 1.0)")
        print("  --invert         Invert colors")
        print("  --clean          Delete temp files")
        return 0

    global XTC_MODE, DITHER_ALGO, GAMMA_VALUE, INVERT_COLORS, FPS_VALUE, TARGET_WIDTH, TARGET_HEIGHT
    
    clean_temp = "--clean" in sys.argv
    INVERT_COLORS = "--invert" in sys.argv
    
    if "--2bit" in sys.argv: XTC_MODE = "2bit"
    
    i = 1
    files = []
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--dither":
            DITHER_ALGO = sys.argv[i+1]
            i += 1
        elif arg == "--gamma":
            GAMMA_VALUE = float(sys.argv[i+1])
            i += 1
        elif arg == "--fps":
            FPS_VALUE = float(sys.argv[i+1])
            i += 1
        elif arg == "--device":
            dev = sys.argv[i+1].upper()
            if dev in DEVICE_DIMENSIONS:
                TARGET_WIDTH, TARGET_HEIGHT = DEVICE_DIMENSIONS[dev]
            i += 1
        elif not arg.startswith("--"):
            files.append(Path(arg))
        i += 1
        
    if not files:
        # Check cwd for videos
        cwd = Path.cwd()
        exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        files = [f for f in cwd.iterdir() if f.suffix.lower() in exts]
        
    if not files:
        print("No video files found.")
        return 1
        
    base_dir = Path.cwd()
    output_dir = base_dir / "xtc_output"
    temp_dir = base_dir / ".temp_video"
    output_dir.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)
    
    for idx, f in enumerate(files, 1):
        process_file(f, output_dir, temp_dir, clean_temp, idx, len(files))
        
    if clean_temp:
        try: shutil.rmtree(temp_dir)
        except: pass
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
