#!/usr/bin/env python3
"""
web2xtc - Convert Websites to XTC/XTCH format for XTEink X4
Uses Playwright to capture full-page screenshots and converts them.

Usage:
    web2xtc <url>              # Process URL
    web2xtc <url> --manhwa     # Process as Manhwa (stitched)
    web2xtc <url> --viewport mobile  # Use mobile viewport
"""

import os
import sys
import shutil
import struct
import hashlib
import re
import json
import numpy as np
try:
    from numba import njit
except ImportError:
    # Fallback to a dummy decorator if numba is missing
    def njit(func):
        return func
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageOps, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Disable Decompression Bomb Error for large webtoons
Image.MAX_IMAGE_PIXELS = None


# Configuration
DEVICE_DIMENSIONS = {
    'X4': (480, 800),
    'X3': (528, 792)
}

TARGET_WIDTH, TARGET_HEIGHT = DEVICE_DIMENSIONS['X4']

# Global configuration (defaults)
XTC_MODE = "1bit"        # "1bit" or "2bit"
DITHER_ALGO = "stucki"    # "floyd", "ordered", "rasterize", "none", "atkinson", "stucki"
DOWNSCALE_FILTER = Image.Resampling.BICUBIC # Default downscaling filter
GAMMA_VALUE = 1.0        # Gamma correction value (1.0 = neutral)
INVERT_COLORS = False    # Invert colors (White <-> Black)
VIEWPORT = "desktop"     # desktop or mobile
COOKIES_FILE = None      # Path to Netscape formatted cookies file
DYNAMIC_MODE = False     # Dynamic crawling mode
PARALLEL_LINKS = False   # Parallelize link crawling
WEBSITE_MODE = None      # Specific website handling (e.g. 'wikipedia')

# Common User Agent for Desktop Spoofing
UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

# Dithering options mapping
DITHER_MAP = {
    'floyd': Image.Dither.FLOYDSTEINBERG,
    'ordered': Image.Dither.ORDERED,
    'rasterize': Image.Dither.RASTERIZE,
    'none': Image.Dither.NONE,
    'atkinson': 'atkinson', # Custom implementation
    'stucki': 'stucki', # Custom implementation
    'ostromoukhov': 'ostromoukhov', # Custom implementation
    'zhoufang': 'zhoufang' # Custom implementation
}

# Downscaling options mapping
DOWNSCALE_MAP = {
    'bicubic': Image.Resampling.BICUBIC,
    'bilinear': Image.Resampling.BILINEAR,
    'box': Image.Resampling.BOX,
    'lanczos': Image.Resampling.LANCZOS,
    'nearest': Image.Resampling.NEAREST
}


def parse_netscape_cookies(cookie_file):
    """Parse Netscape/Mozilla cookie file format into Playwright list of dicts."""
    cookies = []
    try:
        with open(cookie_file, 'r') as f:
            for line in f:
                if line.startswith('#') or not line.strip(): continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    domain = parts[0]
                    # Netscape format: domain, flag, path, secure, expiration, name, value
                    cookie = {
                        'domain': domain,
                        'path': parts[2],
                        'secure': parts[3].upper() == 'TRUE',
                        'expires': int(parts[4]) if parts[4] != "0" else -1,
                        'name': parts[5],
                        'value': parts[6]
                    }
                    # Playwright requires url or domain. Domain starting with dot requires handled carefully
                    # usually just passing domain works if formatted right.
                    cookies.append(cookie)
    except Exception as e:
        print(f"Warning: Failed to parse cookie file: {e}")
    return cookies


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
                # Row 1
                if x + 1 < w: data[idx + 1] += (err * 16) // 103
                if x + 2 < w: data[idx + 2] += (err * 9) // 103
                # Row 2
                idx_n = idx + stride
                if idx_n < len(data):
                    if x - 2 > 0: data[idx_n - 2] += (err * 5) // 103
                    if x - 1 > 0: data[idx_n - 1] += (err * 11) // 103
                    data[idx_n] += (err * 16) // 103
                    if x + 1 < w: data[idx_n + 1] += (err * 11) // 103
                    if x + 2 < w: data[idx_n + 2] += (err * 5) // 103
                # Row 3
                idx_n2 = idx + (stride * 2)
                if idx_n2 < len(data):
                    if x - 2 > 0: data[idx_n2 - 2] += (err * 3) // 103
                    if x - 1 > 0: data[idx_n2 - 1] += (err * 5) // 103
                    data[idx_n2] += (err * 9) // 103
                    if x + 1 < w: data[idx_n2 + 1] += (err * 5) // 103
                    if x + 2 < w: data[idx_n2 + 2] += (err * 3) // 103

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
            
            # Thresholding / Quantization
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
    """
    Apply Stucki dithering to a grayscale PIL image.
    """
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
    """
    Core loop for Atkinson dithering, optimized with Numba.
    Image starts at x=1 to allow Bottom-Left (x-1) without row wrap.
    """
    for y in range(h):
        row_start = y * stride
        for x in range(1, w + 1):
            idx = row_start + x
            old_val = data[idx]
            
            # Thresholding / Quantization
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
                    # Atkinson Kernel (1/8)
                    # Current Row
                    data[idx + 1] += err8
                    data[idx + 2] += err8
                    
                    # Next Row
                    idx_n = idx + stride
                    data[idx_n - 1] += err8 # Bottom-Left
                    data[idx_n]     += err8 # Bottom-Mid
                    data[idx_n + 1] += err8 # Bottom-Right
                    
                    # 2 Rows Down
                    data[idx_n + stride] += err8 # Bottom-Bottom-Mid


def dither_atkinson(img, levels):
    """
    Apply Atkinson dithering to a grayscale PIL image (Optimized with Numba).
    """
    w, h = img.size
    
    # Create a padded buffer (int16 to handle error overflow)
    # x=0: left padding (for bottom-left)
    # x=1..w: image
    # x=w+1..w+2: right padding (for right 1, right 2)
    # y=h..h+2: bottom padding
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    
    # Copy image data into buffer at x=1
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    
    # Flatten for Numba
    data = buff.flatten()
    
    is_2bit = (len(levels) > 2)
    _atkinson_loop(data, w, h, stride, is_2bit)

    # Reconstruct and crop
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    
    return Image.fromarray(final_arr, 'L')


def png_to_xtg_bytes(img: Image.Image, force_size=None, threshold=128):
    """Convert PIL image to XTG bytes (1-bit monochrome)."""
    if force_size is None:
        force_size = (TARGET_WIDTH, TARGET_HEIGHT)

    if img.size != force_size:
        img = img.resize(force_size, DOWNSCALE_FILTER)

    # Ensure 1-bit mode efficiently
    if img.mode != '1':
        img = img.convert("L").point(lambda p: 255 if p >= threshold else 0).convert("1")
    
    data = img.tobytes()
    md5digest = hashlib.md5(data).digest()[:8]
    data_size = len(data)

    # XTG header: <4sHHBBI8s> little-endian
    header = struct.pack(
        "<4sHHBBI8s",
        b"XTG\x00",
        force_size[0],
        force_size[1],
        0,  # colorMode
        0,  # compression
        data_size,
        md5digest,
    )
    return header + data


def png_to_xth_bytes(img: Image.Image, force_size=None):
    """
    Convert PIL image to XTH bytes (2-bit grayscale, planar).
    Follows 'cli/encoder.js' from epub-to-xtc-converter:
    - Vertical scan, Columns Right-to-Left
    - 2 bit planes
    - LUT: White=0(00), Light=1(01), Dark=2(10), Black=3(11)
    """
    if force_size is None:
        force_size = (TARGET_WIDTH, TARGET_HEIGHT)

    if img.size != force_size:
        img = img.resize(force_size, DOWNSCALE_FILTER)

    # Use numpy for fast bit manipulation
    arr = np.array(img.convert('L'))
    
    # Map grayscale to 2-bit value
    # White (>212) -> 0 (00)
    # Light (>127) -> 1 (01)
    # Dark  (>42)  -> 2 (10)
    # Black (else) -> 3 (11)
    vals = np.zeros_like(arr, dtype=np.uint8)
    vals[arr < 42] = 3
    vals[(arr >= 42) & (arr < 127)] = 2
    vals[(arr >= 127) & (arr < 212)] = 1
    vals[arr >= 212] = 0
    
    # Vertical scan, Columns Right to Left
    # 1. Flip horizontally (Right to Left)
    # 2. Transpose (Vertical scan: columns become rows)
    vals = vals[:, ::-1].T
    
    # Plane 0: Bit 0
    # Plane 1: Bit 1
    p0 = (vals & 1).astype(np.uint8)
    p1 = ((vals & 2) >> 1).astype(np.uint8)
    
    # Pack bits into bytes (MSB first)
    plane0 = np.packbits(p0, axis=1).tobytes()
    plane1 = np.packbits(p1, axis=1).tobytes()

    data = plane0 + plane1
    md5digest = hashlib.md5(data).digest()[:8]
    data_size = len(data)

    # XTH header: <4sHHBBI8s> little-endian
    header = struct.pack(
        "<4sHHBBI8s",
        b"XTH\x00",
        force_size[0],
        force_size[1],
        0,  # colorMode
        0,  # compression
        data_size,
        md5digest,
    )
    return header + data


def build_xtc_internal(png_paths, out_path, mode="1bit", toc=None):
    """
    Build XTC/XTCH file internally.
    Strictly follows XTC Format Technical Specification v1.0 (2025-01).
    [Header (56)] [Metadata (256)] [Chapters (N*96)] [Index] [Data]
    """
    if toc is None:
        toc = []
        
    blobs = [None] * len(png_paths)
    print(f"  Encoding {len(png_paths)} pages ({mode})...", end=" ", flush=True)
    
    def encode_page(args):
        idx, p, mode = args
        try:
            img = Image.open(p)
            if mode == "2bit":
                return idx, png_to_xth_bytes(img)
            else:
                return idx, png_to_xtg_bytes(img)
        except Exception as e:
            print(f"Error encoding {p}: {e}")
            return idx, None

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
        results = list(executor.map(encode_page, [(i, p, mode) for i, p in enumerate(png_paths)]))
        
    for idx, blob in results:
        if blob is None:
            return False
        blobs[idx] = blob

    page_count = len(blobs)
    
    # Offsets based on Specification
    header_size = 56
    metadata_size = 256
    chapter_count = len(toc)
    chapter_entry_size = 96
    chapters_size = chapter_count * chapter_entry_size
    index_entry_size = 16
    index_size = page_count * index_entry_size
    
    metadata_offset = header_size
    chapter_offset = metadata_offset + metadata_size
    index_offset = chapter_offset + chapters_size
    data_offset = index_offset + index_size

    # Index table: <Q I H H> per page
    index_table = bytearray()
    rel_offset = data_offset
    
    for blob in blobs:
        # Extract w, h from blob header (offset 4, 2 unsigned shorts)
        w, h = struct.unpack_from("<HH", blob, 4)
        entry = struct.pack("<Q I H H", rel_offset, len(blob), w, h)
        index_table += entry
        rel_offset += len(blob)

    # Magic: XTCH for 2bit, XTC\0 for 1bit
    magic = b"XTCH" if mode == "2bit" else b"XTC\x00"

    # XTC header: <4sHHBBBBIQQQQQ> little-endian (56 bytes)
    xtc_header = struct.pack(
        "<4sHHBBBBIQQQQQ",
        magic,
        1,              # version
        page_count,
        0,              # readDirection (0=L-R)
        1,              # hasMetadata
        0,              # hasThumbnails
        1 if chapter_count > 0 else 0,  # hasChapters
        1,              # currentPage (1-indexed)
        metadata_offset,
        index_offset,
        data_offset,
        0,              # reserved (thumbOffset)
        chapter_offset
    )
    
    # Metadata Block (256 bytes)
    # Spec: 0x00 title (128), 0x80 author (64), 0xC0 publisher (32), 0xE0 language (16)
    #       0xF0 createTime (4), 0xF4 coverPage (2), 0xF6 chapterCount (2), 0xF8 reserved (8)
    metadata = bytearray(256)
    title = os.path.basename(out_path).encode('utf-8')[:127]
    metadata[0:len(title)] = title
    
    # Timestamp at 240 (0xF0)
    timestamp = int(time.time())
    struct.pack_into("<I", metadata, 240, timestamp)
    # Chapter count at 246 (0xF6)
    struct.pack_into("<H", metadata, 246, chapter_count)
    
    # Chapters Block
    chapters = bytearray(chapters_size)
    for i, entry in enumerate(toc):
        # Entry size 96: Title (80), StartPage (2), EndPage (2), Padding
        title_bytes = entry['title'].encode('utf-8')[:78]
        pos = i * chapter_entry_size
        chapters[pos : pos + len(title_bytes)] = title_bytes
        # Hardware/Encoder.js observation: uses 1-based indexing for jump destination
        start_pg = entry['page']
        end_pg = entry.get('end', start_pg)
        struct.pack_into("<H", chapters, pos + 80, start_pg) 
        struct.pack_into("<H", chapters, pos + 82, end_pg) 

    with open(out_path, "wb") as f:
        f.write(xtc_header)
        f.write(metadata)
        f.write(chapters)
        f.write(index_table)
        for blob in blobs:
            f.write(blob)
            
    print("✓")
    return True


def optimize_image(img_data, output_path_base, page_num, suffix="", overlap_percent=None):
    """
    Optimize image for XTEink X4:
    - crop off image margins (if active)
    - Increase image contrast (unless disabled)
    - Split image in half or overlapping thirds horizontally
    - Rotate each half 90° clockwise
    - Resize to fit 480x800 with white padding
    - Convert to grayscale/2-bit
    - Save as PNG (for XTC conversion)
    """
    v_overlap = overlap_percent if overlap_percent is not None else MINIMUM_V_OVERLAP_PERCENT
    if MANHWA and overlap_percent is None:
        v_overlap = 50
    try:
        from io import BytesIO
        uncropped_img = Image.open(BytesIO(img_data))

        if suffix == "_s1":
            #left half of a spread
            width, height = uncropped_img.size
            uncropped_img = uncropped_img.crop((int(0/100.0*width), int(0/100.0*height), width-int(50/100.0*width), height-int(0/100.0*height)))
        if suffix == "_s2":
            #right half of a spread
            width, height = uncropped_img.size
            uncropped_img = uncropped_img.crop((int(50/100.0*width), int(0/100.0*height), width-int(0/100.0*width), height-int(0/100.0*height)))

        if SKIP_ON:
            if str(page_num) in SKIP_PAGES: 
                print("skipping page:",page_num)
                return 0

        if START_PAGE and page_num < START_PAGE:
            # we haven't reached the start page yet
            return 0

        if STOP_PAGE and page_num > STOP_PAGE:
            # we've passed the stop page.
            return 0

        if ONLY_ON:
            if str(page_num) not in ONLY_PAGES: 
                return 0

        if SAMPLE_SET:
            if str(page_num) in SAMPLE_PAGES:
                if uncropped_img.mode != 'L':
                    uncropped_img = uncropped_img.convert('L')
                
                try:
                    font = ImageFont.load_default(size=100)
                except:
                    font = ImageFont.load_default()

                text_color = 0
                box_color = 255
                print("creating samples for page:",page_num)
                width, height = uncropped_img.size
                text_position = (width//8,height//2)
                box_position = ((width//8)-30, (height//2), (width//8)+496, (height//2)+120)
                width_proportion = width / 800
                overlapping_third_height = 480 * width_proportion // 1
                shiftdown_to_overlap = overlapping_third_height - (overlapping_third_height * 3 - height) // 2
                contrast_set = 0
                while contrast_set < 9:
                    black_cutoff = 3 * contrast_set
                    white_cutoff = 3 + 9 * contrast_set
                    page_view = ImageOps.autocontrast(uncropped_img, cutoff=(black_cutoff,white_cutoff), preserve_tone=True)
                    draw = ImageDraw.Draw(page_view)
                    draw.rounded_rectangle(box_position, radius=60, fill=box_color, outline=text_color, width=6, corners=(False,True,False,True))
                    draw.text(text_position, f"Contrast {contrast_set}", fill=text_color, font=font)
                    output_page = output_path_base.parent / f"{page_num:04d}_0_contrast{contrast_set}.png"
                    save_with_padding(page_view, output_page, padcolor=PADDING_COLOR)
                    middle_third = page_view.crop((0, shiftdown_to_overlap, width, height - shiftdown_to_overlap))
                    middle_rotated = middle_third.rotate(-90, expand=True)
                    output_middle = output_path_base.parent / f"{page_num:04d}_3_b_contrast{contrast_set}.png"
                    save_with_padding(middle_rotated, output_middle, padcolor=PADDING_COLOR)
                    contrast_set += 1
                crop_set = 0.0
                contrast3img = ImageOps.autocontrast(uncropped_img, cutoff=(9,30), preserve_tone=True)
                while crop_set < 10:
                    allaroundcrop = crop_set
                    page_view = contrast3img.crop((int(allaroundcrop/100.0*width), int(allaroundcrop/100.0*height), width-int(allaroundcrop/100.0*width), height-int(allaroundcrop/100.0*height)))
                    draw = ImageDraw.Draw(page_view)
                    draw.rounded_rectangle(box_position, radius=60, fill=box_color, outline=text_color, width=6, corners=(False,True,False,True))
                    draw.text(text_position, f"Margin {crop_set}", fill=text_color, font=font)
                    output_page = output_path_base.parent / f"{page_num:04d}_9_margin{crop_set}.png"
                    save_with_padding(page_view, output_page, padcolor=30)                    
                    crop_set += 0.5
            else:
                pass
            return 0

        #enhance contrast
        if CONTRAST_BOOST:
            if CONTRAST_VALUE == "0":
                pass  # we don't need to adjust contrast at all.
            elif len(CONTRAST_VALUE.split(',')) > 1:
                #passed a list of 2, first is dark cutoff, second is bright cutoff.
                black_cutoff = 3 * int(CONTRAST_VALUE.split(',')[0])
                white_cutoff = 3 + 9 * int(CONTRAST_VALUE.split(',')[1])
                uncropped_img = ImageOps.autocontrast(uncropped_img, cutoff=(black_cutoff,white_cutoff), preserve_tone=True)
            elif int(CONTRAST_VALUE) < 0 or int(CONTRAST_VALUE) > 8:
                pass # value out of range. we'll treat like 0.
            else:
                black_cutoff = 3 * int(CONTRAST_VALUE)
                white_cutoff = 3 + 9 * int(CONTRAST_VALUE)
                uncropped_img = ImageOps.autocontrast(uncropped_img, cutoff=(black_cutoff,white_cutoff), preserve_tone=True)
        # else:
            # Default contrast boost DISABLED (User requested "turn off contrast boost")
            # black_cutoff = 3 * 4
            # white_cutoff = 3 + 9 * 4
            # uncropped_img = ImageOps.autocontrast(uncropped_img, cutoff=(black_cutoff,white_cutoff), preserve_tone=True)

        # Convert to grayscale
        if uncropped_img.mode != 'L':
            uncropped_img = uncropped_img.convert('L')
        
        img = uncropped_img
        width, height = img.size

        # Detect solid color pages (blank/filler)
        # Low standard deviation means the image is mostly one color.
        img_arr = np.array(img)
        is_solid = np.std(img_arr) < 5.0

        #crop margins in percentage. 
        if MARGIN:
            if MARGIN_VALUE == "0":
                pass #we don't need to do margins at all.
            elif MARGIN_VALUE.lower() == "auto":
                # trim white space from all four sides.
                invert_img=ImageOps.invert(uncropped_img) #invert image
                invert_img=ImageOps.autocontrast(invert_img,cutoff=(59,40))
                image_box_coords = invert_img.getbbox() # bounding rect around anything not true black.
                img = uncropped_img.crop(image_box_coords)
            elif len(MARGIN_VALUE.split(',')) > 1:
                marginlist = MARGIN_VALUE.split(',')
                marginlist.append("0")
                marginlist.append("0") # 2 0s just in case there aren't four values.
                leftcrop = float(marginlist[0])
                topcrop = float(marginlist[1])
                rightcrop = float(marginlist[2])
                bottomcrop = float(marginlist[3])
                img = uncropped_img.crop((int(leftcrop/100.0*width), int(topcrop/100.0*height), width-int(rightcrop/100.0*width), height-int(bottomcrop/100.0*height)))
            else:
                allaroundcrop = float(MARGIN_VALUE);
                img = uncropped_img.crop((int(allaroundcrop/100.0*width), int(allaroundcrop/100.0*height), width-int(allaroundcrop/100.0*width), height-int(allaroundcrop/100.0*height)))

        width, height = img.size
        
        # Handle landscape images (Spreads)
        is_landscape = width >= height

        # We split most pages that are vertical. 
        should_this_split = True if not is_solid else False
        
        if str(page_num) in SPLIT_SPREADS_PAGES:
            if suffix == "":  
                # we haven't recursed, this is top level.
                should_this_split = False  #we're not splitting this vertically, we're halving it, then the halves will be split recursively.
            else:
                # we have recursed.
                should_this_split = True  #we can't recurse again, it's been halved, it must be split.
        
        if suffix == "" and str(page_num) in DONT_SPLIT_PAGES:  
            #also easy, we don't split. Overrides everything. (excepting recursion)
            should_this_split = False

        if should_this_split:
            # Process overview page BEFORE splits (forced for landscape spreads)
            if INCLUDE_OVERVIEWS or SIDEWAYS_OVERVIEWS or SELECT_OVERVIEWS or is_landscape:
                if SELECT_OVERVIEWS and (str(page_num) not in SELECT_OV_PAGES):
                    pass
                else:
                    # Process overview page
                    page_view = uncropped_img;
                    # Portrait overviews are sideways (-90) by default unless SIDEWAYS_OVERVIEWS is set.
                    # Landscape overviews are always sideways to fit the spread on screen.
                    # Solid pages are always sideways.
                    if is_landscape or is_solid or not SIDEWAYS_OVERVIEWS:
                        page_view = uncropped_img.rotate(-90, expand=True)
                    output_page = output_path_base.parent / f"{page_num:04d}{suffix}_0_overview.png"
                    save_with_padding(page_view, output_page, padcolor=PADDING_COLOR)

        if is_landscape:
            # Rotate landscape pages -90 first so they can be treated as tall portrait pages
            img = img.rotate(-90, expand=True)
            width, height = img.size

        half_height = height // 2
        total_size = 0

        if should_this_split:
            if OVERLAP or DESIRED_V_OVERLAP_SEGMENTS or SET_H_OVERLAP_SEGMENTS or is_landscape:
                # Use overlapping segments
                sw = TARGET_HEIGHT
                sh = TARGET_WIDTH
                
                number_of_h_segments = SET_H_OVERLAP_SEGMENTS
                total_calculated_width = sw * number_of_h_segments - int((number_of_h_segments - 1) * (sw * 0.01 * SET_H_OVERLAP_PERCENT))
                established_scale = total_calculated_width * 1.0 / width

                overlapping_width = sw / established_scale // 1
                shiftover_to_overlap = 0
                if number_of_h_segments > 1:
                    shiftover_to_overlap = overlapping_width - (overlapping_width * number_of_h_segments - width) // (number_of_h_segments - 1)

                # For landscape spreads, we want at least 3 segments to ensure good zoom
                number_of_v_segments = (DESIRED_V_OVERLAP_SEGMENTS - 1) if not is_landscape else 2
                letter_keys = ["a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z"]

                overlapping_height = sh / established_scale // 1
                
                # Ensure we have enough segments to cover the full height without gaps
                shiftdown_to_overlap = 99999
                while number_of_v_segments < 26:
                    number_of_v_segments += 1
                    if number_of_v_segments > 1:
                        shiftdown_to_overlap = overlapping_height - (overlapping_height * number_of_v_segments - height) // (number_of_v_segments - 1)
                    else:
                        shiftdown_to_overlap = 0
                    
                    if shiftdown_to_overlap <= overlapping_height:
                        if (shiftdown_to_overlap * 1.0 / overlapping_height) <= (1.0 - .01 * v_overlap):
                            break

                # Make overlapping segments that fill screen.
                # In rotated image (-90), Top (v=0) is Left, Bottom (v=max) is Right.
                v_list = list(range(number_of_v_segments))
                # Default is LTR (Left then Right). RTL flag reverses this.
                is_rtl = LANDSCAPE_RTL and not MANHWA
                if is_rtl:
                    v_list.reverse() # RTL: Left then Right (Swapped)

                for v_idx, v in enumerate(v_list):
                    h = 0
                    while h < number_of_h_segments:
                        segment = img.crop((shiftover_to_overlap*h, shiftdown_to_overlap*v, width-(shiftover_to_overlap*(number_of_h_segments-h-1)), height-(shiftdown_to_overlap*(number_of_v_segments-v-1))))
                        # Landscape segments rotate 90 to make them upright portrait (0).
                        # Portrait segments: upright (0) if MANHWA, else sideways (-90).
                        rot = 90 if is_landscape else (0 if MANHWA else -90)
                        segment_rotated = segment.rotate(rot, expand=True)
                        
                        if number_of_h_segments > 1:
                            output = output_path_base.parent / f"{page_num:04d}{suffix}_3_{letter_keys[v_idx]}_{letter_keys[h]}.png"
                        else:
                            output = output_path_base.parent / f"{page_num:04d}{suffix}_3_{letter_keys[v_idx]}.png"
                        size = save_with_padding(segment_rotated, output, padcolor=PADDING_COLOR)
                        h += 1

            else:
                # Top half is Right (if landscape base -90) or Top (if portrait)
                part1 = img.crop((0, 0, width, half_height))
                # Bottom half is Left (if landscape base -90) or Bottom (if portrait)
                part2 = img.crop((0, half_height, width, height))
                
                # Landscape segments rotate 90 (to become 0 upright).
                # Portrait segments: upright (0) if MANHWA, else sideways (-90).
                rot = 90 if is_landscape else (0 if MANHWA else -90)
                part1_rotated = part1.rotate(rot, expand=True)
                part2_rotated = part2.rotate(rot, expand=True)
                
                # Default is LTR. RTL reverses this for landscape.
                is_rtl = LANDSCAPE_RTL and not MANHWA
                if is_landscape:
                    if is_rtl:
                        # RTL: Left (part2) then Right (part1)
                        first, second = part2_rotated, part1_rotated
                    else:
                        # LTR: Right (part1) then Left (part2)
                        first, second = part1_rotated, part2_rotated
                else:
                    # Portrait: always Top (part1) then Bottom (part2)
                    first, second = part1_rotated, part2_rotated
                
                out1 = output_path_base.parent / f"{page_num:04d}{suffix}_2_a.png"
                out2 = output_path_base.parent / f"{page_num:04d}{suffix}_2_b.png"
                
                total_size += save_with_padding(first, out1, padcolor=PADDING_COLOR)
                total_size += save_with_padding(second, out2, padcolor=PADDING_COLOR)
        
        else: 
            # This is a dont-split page, treat like overview page
            page_view = uncropped_img;
            # Portrait overviews are sideways (-90) by default unless SIDEWAYS_OVERVIEWS is set.
            # Landscape overviews are always sideways to fit the spread on screen.
            # Solid pages are always sideways.
            if is_landscape or is_solid or not SIDEWAYS_OVERVIEWS:
                page_view = uncropped_img.rotate(-90, expand=True)
            output_page = output_path_base.parent / f"{page_num:04d}{suffix}_0_overview.png"
            save_with_padding(page_view, output_page, padcolor=PADDING_COLOR)

        return total_size
        
    except Exception as e:
        print(f"    Warning: Could not optimize image: {e}")
        return 0


def save_with_padding(img, output_path, *, padcolor=255):
    """
    Resize image to fit within 480x800 and add white padding.
    Applies 1-bit or 2-bit conversion with selected dithering.
    """
    img_width, img_height = img.size
    scale = min(TARGET_WIDTH / img_width, TARGET_HEIGHT / img_height)
    
    new_width = int(img_width * scale)
    new_height = int(img_height * scale)
    
    img_resized = img.resize((new_width, new_height), DOWNSCALE_FILTER)
    
    # Create background (default padcolor is white)
    result = Image.new('L', (TARGET_WIDTH, TARGET_HEIGHT), color=padcolor)
    
    # Center the image
    x = (TARGET_WIDTH - new_width) // 2
    y = (TARGET_HEIGHT - new_height) // 2
    
    result.paste(img_resized, (x, y))
    
    # Apply Color Inversion if requested (Fixes inverted inputs)
    if INVERT_COLORS:
        result = ImageOps.invert(result)
    
    # Apply gamma correction if needed
    if GAMMA_VALUE != 1.0:
        # gamma < 1.0 brightens, > 1.0 darkens
        # Pre-calculate LUT for performance
        lut = [int(((i / 255.0) ** GAMMA_VALUE) * 255.0) for i in range(256)]
        result = result.point(lut)

    # Apply dithering/conversion logic
    if XTC_MODE == "2bit":
        # 2-bit conversion (4 levels)
        
        if DITHER_ALGO == 'none':
            # Use direct thresholding (LUT) for clean, sharp output (Best for text)
            lut = []
            for i in range(256):
                if i < 42:
                    val = 0     # Black
                elif i < 127:
                    val = 85    # Dark Gray
                elif i < 212:
                    val = 170   # Light Gray
                else:
                    val = 255   # White
                lut.append(val)
            result = result.point(lut)
        
        elif DITHER_ALGO == 'atkinson':
            # Custom Atkinson Dithering
            # Process in RGB space? No, Atkinson is usually single-channel error diffusion.
            # result is 'L' mode here.
            result = dither_atkinson(result, levels=[0, 85, 170, 255])
            
        elif DITHER_ALGO == 'stucki':
            result = dither_stucki(result, levels=[0, 85, 170, 255])
            
        elif DITHER_ALGO == 'ostromoukhov':
            result = dither_ostromoukhov(result, levels=[0, 85, 170, 255])
            
        elif DITHER_ALGO == 'zhoufang':
            result = dither_zhoufang(result, levels=[0, 85, 170, 255])
            
        else:
            # Use Floyd-Steinberg Dithering (Best for photos/gradients)
            # Create a 4-color palette image
            pal_img = Image.new("P", (1, 1))
            pal_img.putpalette([0,0,0, 85,85,85, 170,170,170, 255,255,255] + [0,0,0]*252)
            
            # Force Floyd-Steinberg if not 'none' (PIL quantize mainly supports FS)
            # To improve quality, ensure we process in RGB then quantize.
            # Converting to RGB first is CRITICAL: quantization behaves differently/incorrectly on 'L' images.
            result_rgb = result.convert('RGB')
            result = result_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
            result = result.convert('L')
        
    else:
        # 1-bit conversion (Default)
        if DITHER_ALGO == 'atkinson':
            result = dither_atkinson(result, levels=[0, 255])
            # Convert to 1-bit (threshold 128) just to be safe/compliant with '1' mode expectation?
            # Actually, dither_atkinson returns L with values 0 or 255.
            # We can convert to '1' with dither=NONE to pack it.
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
        
        # Convert back to grayscale
        result = result.convert('L')

    result.save(output_path, 'PNG')
    
    return output_path.stat().st_size


def preprocess_for_manhwa(img_data, page_num):
    """
    Preprocess image for Manhwa stitching:
    - Load
    - Contrast Boost
    - Grayscale
    - Margin Crop (if enabled)
    - Rotate if Landscape
    - Resize to width 480
    Returns PIL Image or None
    """
    try:
        from io import BytesIO
        uncropped_img = Image.open(BytesIO(img_data))
        
        # Checks
        if SKIP_ON and str(page_num) in SKIP_PAGES: return None
        if START_PAGE and page_num < START_PAGE: return None
        if STOP_PAGE and page_num > STOP_PAGE: return None
        if ONLY_ON and str(page_num) not in ONLY_PAGES: return None
        
        # Contrast
        if CONTRAST_BOOST:
            if CONTRAST_VALUE == "0": pass
            elif len(CONTRAST_VALUE.split(',')) > 1:
                black_cutoff = 3 * int(CONTRAST_VALUE.split(',')[0])
                white_cutoff = 3 + 9 * int(CONTRAST_VALUE.split(',')[1])
                uncropped_img = ImageOps.autocontrast(uncropped_img, cutoff=(black_cutoff,white_cutoff), preserve_tone=True)
            elif 0 <= int(CONTRAST_VALUE) <= 8:
                black_cutoff = 3 * int(CONTRAST_VALUE)
                white_cutoff = 3 + 9 * int(CONTRAST_VALUE)
                uncropped_img = ImageOps.autocontrast(uncropped_img, cutoff=(black_cutoff,white_cutoff), preserve_tone=True)

        # Grayscale
        if uncropped_img.mode != 'L':
            uncropped_img = uncropped_img.convert('L')
        
        img = uncropped_img
        width, height = img.size
        
        # Margin Crop
        if MARGIN:
            if MARGIN_VALUE == "0": pass
            elif MARGIN_VALUE.lower() == "auto":
                invert_img=ImageOps.invert(uncropped_img)
                invert_img=ImageOps.autocontrast(invert_img,cutoff=(59,40))
                image_box_coords = invert_img.getbbox()
                img = uncropped_img.crop(image_box_coords)
            elif len(MARGIN_VALUE.split(',')) > 1:
                m = MARGIN_VALUE.split(',') + ["0","0"]
                l, t, r, b = float(m[0]), float(m[1]), float(m[2]), float(m[3])
                img = uncropped_img.crop((int(l/100.0*width), int(t/100.0*height), width-int(r/100.0*width), height-int(b/100.0*height)))
            else:
                crop = float(MARGIN_VALUE)
                img = uncropped_img.crop((int(crop/100.0*width), int(crop/100.0*height), width-int(crop/100.0*width), height-int(crop/100.0*height)))
        
        # Resize to Target Width (480)
        w, h = img.size
        scale = TARGET_WIDTH / w
        new_h = int(h * scale)
        img = img.resize((TARGET_WIDTH, new_h), DOWNSCALE_FILTER)
        
        return img
    except Exception as e:
        print(f"Error preprocessing page {page_num}: {e}")
        return None


def process_manhwa_stream(image_iterator, output_folder):
    """
    Process a stream of images as a continuous vertical strip.
    Stitches them together and slices into 480x800 pages.
    Detects solid color pages and accelerates scrolling through them.
    """
    print("  Processing in Manhwa Mode (Continuous Strip)...", end=" ", flush=True)
    
    current_buffer = None # PIL Image
    output_count = 1
    
    slice_height = TARGET_HEIGHT
    overlap_percent = 75
    overlap_pixels = int(slice_height * (overlap_percent / 100.0))
    # Standard step (slow scroll for content)
    standard_step = slice_height - overlap_pixels
    
    page_mapping = {}
    
    for item in image_iterator:
        # Unpack item (handle legacy 2-tuple or new 3-tuple)
        if len(item) == 3:
            img_data, page_num, page_title = item
        else:
            img_data, page_num = item
            page_title = f"Page {page_num}"

        # Record where this source page starts
        page_mapping[str(page_num)] = {'start': output_count, 'title': page_title}
        
        img = preprocess_for_manhwa(img_data, page_num)
        if img is None: continue
        
        if current_buffer is None:
            current_buffer = img
        else:
            # Stitch
            new_h = current_buffer.height + img.height
            new_img = Image.new('L', (TARGET_WIDTH, new_h))
            new_img.paste(current_buffer, (0, 0))
            new_img.paste(img, (0, current_buffer.height))
            current_buffer = new_img
        
        # Slice chunks
        while current_buffer.height >= slice_height:
            chunk = current_buffer.crop((0, 0, TARGET_WIDTH, slice_height))
            
            # Check for solid color in this specific view
            chunk_arr = np.array(chunk)
            is_solid = np.std(chunk_arr) < 5.0
            
            step = slice_height if is_solid else standard_step
            
            # Save
            out_path = output_folder / f"{output_count:05d}.png" 
            save_with_padding(chunk, out_path, padcolor=PADDING_COLOR)
            output_count += 1
            
            # Remove chunk from buffer
            current_buffer = current_buffer.crop((0, step, TARGET_WIDTH, current_buffer.height))
    
    # Handle remainder
    if current_buffer and current_buffer.height > 0:
        out_path = output_folder / f"{output_count:05d}.png"
        save_with_padding(current_buffer, out_path, padcolor=PADDING_COLOR)
    
    # Save mapping
    with open(output_folder / "manhwa_map.json", "w") as f:
        json.dump(page_mapping, f)
        
    print("✓")


def clean_page(page):
    """Perform safe cleanups before capture (e.g. redirect notices)."""
    try:
        page.evaluate("""() => {
            // Remove Wikipedia redirect notice
            const redirectMsg = document.querySelector('.mw-redirectedfrom');
            if (redirectMsg) redirectMsg.style.display = 'none';
            
            // Fallback: Remove small elements containing "redirected from"
            const candidates = document.querySelectorAll('div, span, p, small');
            candidates.forEach(el => {
                if (el.innerText && el.innerText.includes('(redirected from') && el.innerText.length < 200) {
                    el.style.display = 'none';
                }
            });
        }""")
    except: pass


def scroll_page(page):
    """Scroll to bottom to trigger lazy loading."""
    try:
        # Scroll down slowly to trigger lazy loads
        page.evaluate("""async () => {
            await new Promise((resolve) => {
                let totalHeight = 0;
                const distance = 300;
                const timer = setInterval(() => {
                    const scrollHeight = document.body.scrollHeight;
                    window.scrollBy(0, distance);
                    totalHeight += distance;

                    if(totalHeight >= scrollHeight){
                        clearInterval(timer);
                        resolve();
                    }
                }, 100);
            });
        }""")
        time.sleep(3.0) # Wait longer for images to render
    except: pass


def capture_page_worker(args):
    """Worker for parallel link capturing"""
    url, idx, title, viewport, cookies_file = args
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if viewport == "mobile":
                device = p.devices['iPhone 13 Pro']
                # Override viewport to match XTEink X4/X3 pixel mapping
                device['viewport'] = {'width': TARGET_WIDTH, 'height': TARGET_HEIGHT}
                # Disable Retina scaling (DPR=1) for 9x speedup and native 480px width
                device['device_scale_factor'] = 1
                # Keep the Mobile User Agent from the device descriptor
                browser = p.chromium.launch()
                context = browser.new_context(**device)
            else:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={'width': 1280, 'height': 800}, user_agent=UA_DESKTOP)
            
            if cookies_file:
                cookies = parse_netscape_cookies(cookies_file)
                if cookies: context.add_cookies(cookies)
                
            page = context.new_page()
            
            if WEBSITE_MODE == "notion":
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector(".notion-app", timeout=30000)
                    time.sleep(2)
                except: pass
            else:
                page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Pre-processing
            scroll_page(page)
            clean_page(page)
            
            data = page.screenshot(full_page=True, type='png')
            browser.close()
            return (data, idx, title)
    except Exception as e:
        # Fail silently-ish to not break the batch, return None
        return None


def extract_url_to_png(url, temp_dir):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ✗ Error: playwright not found. Please run: pip install playwright && playwright install")
        return None

    name = re.sub(r'[^a-zA-Z0-9]', '_', url.split('//')[-1])[:50]
    output_folder = temp_dir / name
    output_folder.mkdir(parents=True, exist_ok=True)

    print(f"  Loading {url} ({VIEWPORT})...", end=" ", flush=True)

    captures = []

    try:
        # Initial Load & Extraction (Single Threaded)
        with sync_playwright() as p:
            if VIEWPORT == "mobile":
                device = p.devices['iPhone 13 Pro']
                device['viewport'] = {'width': TARGET_WIDTH, 'height': TARGET_HEIGHT}
                device['device_scale_factor'] = 1
                browser = p.chromium.launch()
                context = browser.new_context(**device)
            else:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={'width': 1280, 'height': 800}, user_agent=UA_DESKTOP)

            if COOKIES_FILE:
                cookies = parse_netscape_cookies(COOKIES_FILE)
                if cookies: context.add_cookies(cookies)

            page = context.new_page()
            
            if WEBSITE_MODE == "notion":
                # Notion is an SPA and often never settles networkidle due to background polling
                print("  [Notion] Using SPA loading strategy...", end=" ", flush=True)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    # Wait for the main app container
                    page.wait_for_selector(".notion-app", timeout=30000)
                    # Give it extra time for hydration/rendering
                    time.sleep(5)
                except Exception as e:
                    print(f" (Warning: {e})", end="")
            else:
                page.goto(url, wait_until="networkidle", timeout=60000)
            
            time.sleep(2) 

            if WEBSITE_MODE == 'wikipedia':
                print("\n  [Wikipedia] Expanding sections...", end=" ", flush=True)
                try:
                    page.evaluate("""() => {
                        const wikis = document.querySelectorAll('.collapsible-heading, [aria-expanded="false"]');
                        wikis.forEach(el => {
                            if (el.getAttribute('aria-expanded') === 'false' || el.classList.contains('closed-block')) {
                                el.click();
                            }
                        });
                    }""")
                    time.sleep(1)
                except: pass

            if DYNAMIC_MODE:
                print("\n  [Dynamic] Identifying interactive elements...", end=" ", flush=True)
                try:
                    page.evaluate("""() => {
                        const candidates = Array.from(document.querySelectorAll('button, div[role="button"], span[class*="dropdown"], div[class*="list"]'));
                        const badKeywords = ['menu', 'nav', 'hamburger', 'close', 'login', 'search'];
                        candidates.forEach(el => {
                            if (el.offsetParent === null) return;
                            const text = (el.innerText || "").toLowerCase();
                            const cls = (el.className || "").toLowerCase();
                            const id = (el.id || "").toLowerCase();
                            if (badKeywords.some(k => cls.includes(k) || id.includes(k))) return;
                            if (text.includes('chapter') || text.includes('volume') || cls.includes('chapter') || cls.includes('dropdown')) {
                                el.click();
                            }
                        });
                    }""")
                    time.sleep(2)
                except: pass

                print("Extracting links...", end=" ", flush=True)
                links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .filter(a => a.offsetParent !== null)
                        .map(a => ({href: a.href, text: a.innerText.trim()}))
                        .filter(l => l.text.length > 0 && !l.href.startsWith('javascript'));
                }""")
                
                unique_links = {}
                for l in links:
                    if l['href'] not in unique_links:
                        unique_links[l['href']] = l['text']
                
                print(f" Found {len(unique_links)} links.")
                
                # Capture Main Page
                scroll_page(page)
                clean_page(page)
                captures.append((page.screenshot(full_page=True, type='png'), 1, "Main Page"))
                
                link_items = []
                base_domain = url.split('/')[2]
                for i, (link_url, link_text) in enumerate(unique_links.items(), 2):
                    if base_domain not in link_url: continue
                    link_items.append((link_url, i, link_text[:50]))

                browser.close() # Close initial browser to free resources if needed

                # Crawl Sub-pages
                total = len(link_items)
                if PARALLEL_LINKS and total > 0:
                    print(f"  Capturing {total} sub-pages (Parallel)...")
                    workers = min(4, os.cpu_count() or 2)
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        # Prepare args: url, idx, title, viewport, cookies
                        tasks = [(u, i, t, VIEWPORT, COOKIES_FILE) for u, i, t in link_items]
                        futures = [executor.submit(capture_page_worker, t) for t in tasks]
                        
                        done_count = 0
                        for f in as_completed(futures):
                            res = f.result()
                            done_count += 1
                            print(f"\r    [{done_count}/{total}] Processing...", end="", flush=True)
                            if res: captures.append(res)
                else:
                    # Sequential Reuse (Re-open browser)
                    print(f"  Capturing {total} sub-pages (Sequential)...")
                    with sync_playwright() as p:
                                    if VIEWPORT == "mobile":
                                        device = p.devices['iPhone 13 Pro']
                                        device['viewport'] = {'width': TARGET_WIDTH, 'height': TARGET_HEIGHT}
                        
                            device['device_scale_factor'] = 1
                            browser = p.chromium.launch()
                            context = browser.new_context(**device)
                        else:
                            browser = p.chromium.launch()
                            context = browser.new_context(viewport={'width': 1280, 'height': 800}, user_agent=UA_DESKTOP)
                        if COOKIES_FILE:
                            cookies = parse_netscape_cookies(COOKIES_FILE)
                            if cookies: context.add_cookies(cookies)
                        
                        page = context.new_page()
                        
                        for idx, (link_url, i, link_text) in enumerate(link_items, 1):
                            print(f"\r    [{idx}/{total}] {link_text}...", end="", flush=True)
                            try:
                                if WEBSITE_MODE == "notion":
                                    page.goto(link_url, wait_until="domcontentloaded", timeout=30000)
                                    try:
                                        page.wait_for_selector(".notion-app", timeout=10000)
                                        time.sleep(1)
                                    except: pass
                                else:
                                    page.goto(link_url, wait_until="networkidle", timeout=30000)
                                
                                scroll_page(page)
                                clean_page(page)
                                captures.append((page.screenshot(full_page=True, type='png'), i, link_text))
                            except: pass
                        browser.close()
                print("") # Newline

            else:
                # Standard Mode
                scroll_page(page)
                clean_page(page)
                captures.append((page.screenshot(full_page=True, type='png'), 1, "Main Page"))
                browser.close()

        # Sort captures by index
        captures.sort(key=lambda x: x[1])

        if MANHWA:
            process_manhwa_stream(captures, output_folder)
        else:
            for img_data, idx, title in captures:
                output_base = output_folder / f"{idx:04d}"
                optimize_image(img_data, output_base, idx, overlap_percent=None)

        print("✓")
        return output_folder

    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None


def convert_png_folder_to_xtc(png_folder, output_file, source_file=None):
    """
    Convert folder of PNGs to XTC using internal encoder.
    Generates TOC for chapter navigation.
    """
    # Get all PNG files
    png_files = sorted(png_folder.glob("*.png"))
    if not png_files:
        print(f"  ✗ No PNG files found in {png_folder}")
        return False
    total_files = len(png_files)

    # Build mapping from original page number to first and last segment index
    page_ranges = {}
    manhwa_map_file = png_folder / "manhwa_map.json"
    
    if manhwa_map_file.exists():
        try:
            with open(manhwa_map_file) as f:
                mapping = json.load(f)
            # Reconstruct page_ranges from mapping
            sorted_pages = sorted([int(k) for k in mapping.keys()])
            
            for i, p in enumerate(sorted_pages):
                entry_data = mapping[str(p)]
                # Handle legacy format (int) or new format (dict)
                if isinstance(entry_data, int):
                    start = entry_data
                    title_text = f"Page {p}"
                else:
                    start = entry_data['start']
                    title_text = entry_data.get('title', f"Page {p}")

                # Ensure start is within bounds
                if start > total_files: start = total_files
                
                # End is start of next - 1, or last file
                if i < len(sorted_pages) - 1:
                    next_p = sorted_pages[i+1]
                    next_data = mapping[str(next_p)]
                    end = next_data if isinstance(next_data, int) else next_data['start']
                    
                    if end > total_files: end = total_files
                    
                    if end > start: end -= 1
                    
                    if end < start: end = start
                else:
                    end = total_files
                
                page_ranges[p] = {'start': start, 'end': end, 'title': title_text}
        except Exception as e:
            print(f"Warning: Failed to load manhwa map: {e}")
            pass

    if not page_ranges:
        for idx, p in enumerate(png_files, 1):
            try:
                # Filename format: {page_num:04d}{suffix}_{type}_...
                m = re.match(r'^(\d+)', p.name)
                if not m: continue
                orig_page = int(m.group(1))
                
                if orig_page not in page_ranges:
                    page_ranges[orig_page] = {'start': idx, 'end': idx, 'title': f"Page {orig_page}"}
                else:
                    page_ranges[orig_page]['end'] = idx
            except (ValueError, IndexError):
                continue

    # Generate TOC
    toc = []
    
    # Create TOC entry for every original page
    for orig_page in sorted(page_ranges.keys()):
        entry = page_ranges[orig_page]
        
        # Apply +3 page offset for chapters (User request) - Mobile Viewport Only
        if VIEWPORT == "mobile":
            start_val = min(entry['start'] + 3, total_files)
            end_val = min(entry['end'] + 3, total_files)
        else:
            start_val = entry['start']
            end_val = entry['end']
        
        toc.append({
            "title": entry.get('title', f"Page {orig_page}"),
            "page": start_val,
            "end": end_val
        })

    try:
        # Use internal encoder
        success = build_xtc_internal(png_files, output_file, mode=XTC_MODE, toc=toc)
        
        if success and output_file.exists():
            size_mb = output_file.stat().st_size / 1024 / 1024
            print(f"  ✓ Created {output_file.name} ({size_mb:.1f}MB) with {len(toc)} chapters")
            return True
        else:
            print(f"  ✗ Conversion failed")
            return False
            
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def process_file(input_obj, output_dir, temp_dir, clean_temp, file_num=None, total_files=None):
    progress_prefix = f"[{file_num}/{total_files}] " if file_num and total_files else ""
    is_url = str(input_obj).startswith('http')
    
    if is_url:
        print(f"\n{progress_prefix}Processing URL: {input_obj}")
    else:
        print(f"\n{progress_prefix}Processing: {input_obj.name}")
    
    start_time = time.time()
    
    if is_url:
        png_folder = extract_url_to_png(input_obj, temp_dir)
        name = re.sub(r'[^a-zA-Z0-9]', '_', str(input_obj).split('//')[-1])[:30]
    else:
        # File path
        if input_obj.suffix.lower() == '.pdf':
            # Not supported in web2xtc really, but keeping legacy structure
            # user should use cbz2xtc for files
            print("  ✗ PDF/CBZ not supported in web2xtc (use cbz2xtc)")
            return False, input_obj.name, 0
        else:
            return False, input_obj.name, 0
        
    if not png_folder:
        return False, name, 0
    
    ext = ".xtch" if XTC_MODE == "2bit" else ".xtc"
    output_file = output_dir / f"{name}{ext}"
    
    success = convert_png_folder_to_xtc(png_folder, output_file)
    
    if clean_temp and png_folder.exists():
        shutil.rmtree(png_folder)
    
    elapsed = time.time() - start_time
    return success, name, elapsed


def main():
    print("=" * 60)
    print("Web to XTC Converter for XTEink X4")
    print("=" * 60)
    
    # Check for help flag
    if "--help" in sys.argv or "-h" in sys.argv:
        print("\nConverts Websites to XTC format optimized for XTEink X4 using Playwright")
        print("\nUsage:")
        print("  web2xtc <url>                     # Process URL")
        print("  web2xtc <url> --viewport mobile   # Use mobile viewport (enables Manhwa mode)")
        print("  web2xtc <url> --downscale bilinear # Use bilinear downscaling")
        print("  web2xtc <url> --cookies cookies.txt # Load Netscape cookies")
        print("\nDithering Algorithms:")
        print("  stucki     - Stucki (Default, sharpest)")
        print("  atkinson   - Atkinson (Sharp shading)")
        print("  ostromoukhov - Ostromoukhov (Blue noise, smooth)")
        print("  zhoufang   - Zhou-Fang (High quality, reduced artifacts)")
        print("  floyd      - Floyd-Steinberg (Smooth gradients)")
        print("  ordered    - Ordered/Bayer (Grid pattern)")
        print("  rasterize  - Halftone style")
        print("  none       - Pure threshold")
        print("\nDownscaling Algorithms:")
        print("  bicubic    - Bicubic (Default, sharpest)")
        print("  bilinear   - Bilinear (Smoother)")
        print("  box        - Box (Pixel averaging)")
        print("\nOptions:")
        print("  --no-dither   Same as --dither none")
        print("  --downscale   Select downscaling algorithm (bicubic, bilinear, box, lanczos, nearest)")
        print("  --2bit        Output 2-bit grayscale XTCH files instead of 1-bit XTC.")
        print("                (Dithering works with 2-bit mode too)")
        print("  --viewport <type>   Set viewport: 'desktop' (1280x800, default) or 'mobile' (iPhone 13 Pro)")
        print("  --cookies <file>    Load cookies from a Netscape formatted file (e.g. from extensions)")
        print("\n  --overlap     Split into 3 overlapping screen-filling pieces instead")
        print("                of 2 non-overlapping pieces that may leave margins.")
        print("\n  --split-spreads all or <pagenum> or <pagenum,pagenum,pagenum...>")
        print("                Splits wide pages in half, and then split each of the")
        print("                halves as if they were normal pages.")
        print("\n  --split-all   Splits ALL pages into pieces, even if those pages")
        print("                are wider than they are tall.")
        print("\n  --skip <pagenum> or <pagenum,pagenum,pagenum...>   skips page")
        print("                or pages entirely.")
        print("\n  --only <pagenum> or <pagenum,pagenum,pagenum...>   only renders")
        print("                the selected page or pages.")
        print("\n  --dont-split <pagenum> or <pagenum,pagenum,pagenum...>   don't split")
        print("                page or pages, will use an overview instead.")
        print("\n  --contrast-boost <0-8> or <#,#>   Enhances contrast.")
        print("\n  --margin auto or <float> or <left,top,right,bottom>   crops off")
        print("                page margins by a percentage of the width or height.")
        print("\n  --include-overviews   Show an overview of each page before the")
        print("                split pieces.")
        print("\n  --sideways-overviews   Show a rotated overview of each page.")
        print("\n  --select-overviews <pagenum> ...  Add overview pages for specific pages.")
        print("\n  --start <pagenum>   Don't process pages before this page.")
        print("\n  --stop <pagenum>    Don't process pages after this page.")
        print("\n  --pad-black   Pad things that don't fill screen with black instead")
        print("\n  --hsplit-count <#>   Split page horizontally into # segments.")
        print("\n  --hsplit-overlap <float>   horizontal overlap between segments.")
        print("\n  --hsplit-max-width <#>   limit the width of horizontal segments.")
        print("\n  --vsplit-target <#>   try to split page vertically into # segments.")
        print("\n  --vsplit-min-overlap <float>   minimum vertical overlap between segments.")
        print("\n  --sample-set <pagenum> ...  Build a spread of contrast samples.")
        print("\n  --landscape-rtl   Process landscape spreads from Right to Left.")
        print("\n  --clean       Automatically delete temporary PNG files after conversion.")
        print("\n  --help, -h    Show this help message")
        return 0
    
    global OVERLAP, SPLIT_SPREADS, SPLIT_SPREADS_PAGES, SPLIT_ALL, SKIP_ON, SKIP_PAGES, ONLY_ON, ONLY_PAGES
    global DONT_SPLIT, DONT_SPLIT_PAGES, CONTRAST_BOOST, CONTRAST_VALUE, MARGIN, MARGIN_VALUE
    global INCLUDE_OVERVIEWS, SIDEWAYS_OVERVIEWS, SELECT_OVERVIEWS, SELECT_OV_PAGES
    global START_PAGE, STOP_PAGE, SAMPLE_SET, SAMPLE_PAGES
    global DESIRED_V_OVERLAP_SEGMENTS, SET_H_OVERLAP_SEGMENTS, MINIMUM_V_OVERLAP_PERCENT, SET_H_OVERLAP_PERCENT
    global MAX_SPLIT_WIDTH, PADDING_COLOR, LANDSCAPE_RTL, MANHWA
    global XTC_MODE, DITHER_ALGO, DOWNSCALE_FILTER, GAMMA_VALUE, INVERT_COLORS, VIEWPORT, COOKIES_FILE, DYNAMIC_MODE, PARALLEL_LINKS, WEBSITE_MODE
    global TARGET_WIDTH, TARGET_HEIGHT

    clean_temp = "--clean" in sys.argv
    INVERT_COLORS = "--invert" in sys.argv
    LANDSCAPE_RTL = "--landscape-rtl" in sys.argv
    DYNAMIC_MODE = "--dynamic" in sys.argv
    PARALLEL_LINKS = "--parallel-links" in sys.argv

    if "--device" in sys.argv:
        try:
            idx = sys.argv.index("--device")
            dev = sys.argv[idx+1].upper()
            if dev in DEVICE_DIMENSIONS:
                TARGET_WIDTH, TARGET_HEIGHT = DEVICE_DIMENSIONS[dev]
        except: pass

    if "--downscale" in sys.argv:
        try:
            idx = sys.argv.index("--downscale")
            if idx + 1 < len(sys.argv) and not sys.argv[idx+1].startswith("--"):
                val = sys.argv[idx + 1].lower()
                if val in DOWNSCALE_MAP:
                    DOWNSCALE_FILTER = DOWNSCALE_MAP[val]
                else:
                    print(f"Warning: Unknown downscale filter '{val}', using default 'bicubic'")
            else:
                print("Warning: --downscale flag missing value, using default")
        except IndexError:
            print("Warning: --downscale flag missing value, using default")
    
    input_arg = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ""
    
    if not input_arg:
        print("Usage: web2xtc <url> [options]")
        return 1

    input_list = [input_arg] if input_arg.startswith("http") else []
    
    if not input_list:
        print("Error: Please provide a valid URL starting with http/https")
        return 1

    # Auto-detect website mode
    if "wikipedia.org" in input_list[0] or "wiktionary.org" in input_list[0]:
        WEBSITE_MODE = "wikipedia"
        print(f"Website Mode: {WEBSITE_MODE.upper()} (Auto-detected)")
    elif "notion.site" in input_list[0]:
        WEBSITE_MODE = "notion"
        print(f"Website Mode: {WEBSITE_MODE.upper()} (Auto-detected)")
    
    if "--gamma" in sys.argv:
        try:
            idx = sys.argv.index("--gamma")
            GAMMA_VALUE = float(sys.argv[idx + 1])
        except: pass
    
    if "--no-dither" in sys.argv: DITHER_ALGO = "none"
    if "--dither" in sys.argv:
        try:
            idx = sys.argv.index("--dither")
            val = sys.argv[idx+1].lower()
            if val in DITHER_MAP: DITHER_ALGO = val
        except: pass
    
    if "--2bit" in sys.argv: XTC_MODE = "2bit"
    if "--viewport" in sys.argv:
        try:
            idx = sys.argv.index("--viewport")
            val = sys.argv[idx+1].lower()
            if val in ["mobile", "desktop"]: VIEWPORT = val
        except: pass
        
    if "--cookies" in sys.argv:
        try:
            idx = sys.argv.index("--cookies")
            COOKIES_FILE = sys.argv[idx+1]
        except: pass

    # Bind Manhwa mode to mobile viewport
    MANHWA = (VIEWPORT == "mobile")

    # Set defaults for other globals
    OVERLAP = "--overlap" in sys.argv
    SPLIT_SPREADS = "--split-spreads" in sys.argv
    SPLIT_ALL = "--split-all" in sys.argv
    SKIP_ON = "--skip" in sys.argv
    ONLY_ON = "--only" in sys.argv
    DONT_SPLIT = "--dont-split" in sys.argv
    CONTRAST_BOOST = "--contrast-boost" in sys.argv
    MARGIN = "--margin" in sys.argv
    INCLUDE_OVERVIEWS = "--include-overviews" in sys.argv
    SIDEWAYS_OVERVIEWS = "--sideways-overviews" in sys.argv
    SELECT_OVERVIEWS = "--select-overviews" in sys.argv
    START_PAGE = False; STOP_PAGE = False
    SAMPLE_SET = "--sample-set" in sys.argv
    SPLIT_SPREADS_PAGES = []; SKIP_PAGES = []; ONLY_PAGES = []
    DONT_SPLIT_PAGES = []; SELECT_OV_PAGES = []; SAMPLE_PAGES = []
    DESIRED_V_OVERLAP_SEGMENTS = 3; SET_H_OVERLAP_SEGMENTS = 1
    MINIMUM_V_OVERLAP_PERCENT = 5; SET_H_OVERLAP_PERCENT = 70
    MAX_SPLIT_WIDTH = 800; PADDING_COLOR = 255
    if "--pad-black" in sys.argv: PADDING_COLOR = 0

    # Parse value args (simplified)
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--contrast-boost": CONTRAST_VALUE = sys.argv[i+1]
        elif arg == "--margin": MARGIN_VALUE = sys.argv[i+1]
        elif arg == "--vsplit-target": 
            OVERLAP = True; DESIRED_V_OVERLAP_SEGMENTS = int(sys.argv[i+1])
        elif arg == "--cookies": pass
        i += 1

    input_arg = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ""
    
    if not input_arg:
        print("Usage: web2xtc <url> [options]")
        return 1

    input_list = [input_arg] if input_arg.startswith("http") else []
    
    if not input_list:
        print("Error: Please provide a valid URL starting with http/https")
        return 1

    base_dir = Path.cwd()
    output_dir = base_dir / "xtc_output"
    temp_dir = base_dir / ".temp_web"
    output_dir.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)

    print(f"Input: {input_list[0]}")
    print(f"Viewport: {VIEWPORT.upper()}")
    print(f"Mode: {XTC_MODE}")

    downscale_name = "BICUBIC"
    for k, v in DOWNSCALE_MAP.items():
        if v == DOWNSCALE_FILTER:
            downscale_name = k.upper()
            break
    print(f"Downscaling: {downscale_name}")

    if MANHWA: print("Manhwa Mode: ENABLED")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(process_file, input_list[0], output_dir, temp_dir, clean_temp, 1, 1)
        success, filename, elapsed = future.result()
        if success:
            print(f"  ⏱  {elapsed:.1f}s")

    if clean_temp:
        try: shutil.rmtree(temp_dir)
        except: pass
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
