#!/usr/bin/env python3
"""
image2xth - Convert images to 2-bit 4-level grayscale XTH for XTEink X4
Creates high-quality grayscale images perfect for backgrounds

Usage:
    image2xth image.jpg                       # Default: Atkinson dither, Cover scaling
    image2xth image.jpg --mode letterbox      # Scale to fit with padding
    image2xth image.jpg --pad black           # Black background for letterbox
    image2xth image.jpg --dither floyd        # Floyd-Steinberg dithering
    image2xth image.jpg --gamma 0.7           # Adjust brightness
    image2xth folder/                         # Convert all images in folder

Modes:
    cover (default) - Scale to fill screen and crop overflow (Sharpest fill)
    letterbox       - Scale to fit within screen and add padding
    fill            - Stretch to fill 480x800 (ignores aspect ratio)
    crop            - Center crop 480x800 from original without scaling

Dithering:
    stucki (default), atkinson, ostromoukhov, zhoufang, stochastic (Velho SFC), floyd, none
"""

import os
import sys
import struct
import hashlib
import numpy as np
from pathlib import Path
from PIL import Image, ImageOps

try:
    from numba import njit
except ImportError:
    def njit(func):
        return func

# Global configuration (defaults)
DITHER_ALGO = "stucki" # "atkinson", "floyd", "none"
DOWNSCALE_FILTER = Image.Resampling.BICUBIC # Default downscaling filter

# Downscaling options mapping
DOWNSCALE_MAP = {
    'bicubic': Image.Resampling.BICUBIC,
    'bilinear': Image.Resampling.BILINEAR,
    'box': Image.Resampling.BOX,
    'lanczos': Image.Resampling.LANCZOS,
    'nearest': Image.Resampling.NEAREST
}

# Configuration
TARGET_WIDTH = 480
TARGET_HEIGHT = 800
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff', '.tif'}

@njit
def rot(n, x, y, rx, ry):
    if ry == 0:
        if rx == 1:
            x = n - 1 - x
            y = n - 1 - y
        return y, x
    return x, y

@njit
def d2xy(n, d):
    t = d
    x = 0
    y = 0
    s = 1
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        x, y = rot(s, x, y, rx, ry)
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y

@njit
def _stochastic_loop(data, w, h, stride, is_2bit):
    # Determine N (power of 2) covering the image
    n = 1
    while n < w or n < h:
        n *= 2
    
    acc_err = 0
    total_points = n * n
    
    for d in range(total_points):
        x, y = d2xy(n, d)
        
        # Check if point is within image bounds
        if x < w and y < h:
            # Buffer layout: rows 0..h-1, cols 1..w (0 is padding)
            idx = y * stride + (x + 1)
            
            old_val = data[idx] + acc_err
            
            # Quantize
            if is_2bit:
                # 4 levels: 0, 85, 170, 255
                # Thresholds: 42, 127, 212
                if old_val < 42: new_val = 0
                elif old_val < 127: new_val = 85
                elif old_val < 212: new_val = 170
                else: new_val = 255
            else:
                new_val = 0 if old_val < 128 else 255
            
            data[idx] = new_val
            acc_err = old_val - new_val

def dither_stochastic(img, levels):
    w, h = img.size
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    data = buff.flatten()
    is_2bit = (len(levels) > 2)
    # No random values needed for SFC Error Diffusion
    _stochastic_loop(data, w, h, stride, is_2bit)
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr, 'L')

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
                if x + 1 < w: data[idx + 1] += (err * 8) // 42
                if x + 2 < w: data[idx + 2] += (err * 4) // 42
                idx_n = idx + stride
                if idx_n < len(data):
                    if x - 2 > 0: data[idx_n - 2] += (err * 2) // 42
                    if x - 1 > 0: data[idx_n - 1] += (err * 4) // 42
                    data[idx_n] += (err * 8) // 42
                    if x + 1 < w: data[idx_n + 1] += (err * 4) // 42
                    if x + 2 < w: data[idx_n + 2] += (err * 2) // 42
                idx_n2 = idx + (stride * 2)
                if idx_n2 < len(data):
                    if x - 2 > 0: data[idx_n2 - 2] += (err * 1) // 42
                    if x - 1 > 0: data[idx_n2 - 1] += (err * 2) // 42
                    data[idx_n2] += (err * 4) // 42
                    if x + 1 < w: data[idx_n2 + 1] += (err * 5) // 42
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
                    data[idx_n]     += err8
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

def convert_image(input_path, output_path, dither_algo='atkinson', gamma=1.0, invert=False, mode='cover', pad_color=255, is_xtg=False):
    try:
        img = Image.open(input_path)
        if img.mode != 'L':
            img = img.convert('L')
        
        img_width, img_height = img.size
        
        if mode == 'fill':
            img_resized = img.resize((TARGET_WIDTH, TARGET_HEIGHT), DOWNSCALE_FILTER)
            result = img_resized
        elif mode == 'crop':
            # Center crop
            left = (img_width - TARGET_WIDTH) // 2
            top = (img_height - TARGET_HEIGHT) // 2
            result = img.crop((left, top, left + TARGET_WIDTH, top + TARGET_HEIGHT))
        elif mode == 'cover':
            # Scale to fill and crop overflow
            scale = max(TARGET_WIDTH / img_width, TARGET_HEIGHT / img_height)
            new_width, new_height = int(img_width * scale), int(img_height * scale)
            img_resized = img.resize((new_width, new_height), DOWNSCALE_FILTER)
            left = (new_width - TARGET_WIDTH) // 2
            top = (new_height - TARGET_HEIGHT) // 2
            result = img_resized.crop((left, top, left + TARGET_WIDTH, top + TARGET_HEIGHT))
        else: # letterbox
            scale = min(TARGET_WIDTH / img_width, TARGET_HEIGHT / img_height)
            new_width, new_height = int(img_width * scale), int(img_height * scale)
            img_resized = img.resize((new_width, new_height), DOWNSCALE_FILTER)
            result = Image.new('L', (TARGET_WIDTH, TARGET_HEIGHT), color=pad_color)
            x, y = (TARGET_WIDTH - new_width) // 2, (TARGET_HEIGHT - new_height) // 2
            result.paste(img_resized, (x, y))
        
        if invert:
            result = ImageOps.invert(result)
        
        if gamma != 1.0:
            lut = [int(((i / 255.0) ** gamma) * 255.0) for i in range(256)]
            result = result.point(lut)

        levels = [0, 85, 170, 255] if not is_xtg else [0, 255]

        if dither_algo == 'atkinson':
            result = dither_atkinson(result, levels=levels)
        elif dither_algo == 'stucki':
            result = dither_stucki(result, levels=levels)
        elif dither_algo == 'ostromoukhov':
            result = dither_ostromoukhov(result, levels=levels)
        elif dither_algo == 'zhoufang':
            result = dither_zhoufang(result, levels=levels)
        elif dither_algo == 'stochastic':
            result = dither_stochastic(result, levels=levels)
        elif dither_algo == 'floyd':
            if not is_xtg:
                pal_img = Image.new("P", (1, 1))
                pal_img.putpalette([0,0,0, 85,85,85, 170,170,170, 255,255,255] + [0,0,0]*252)
                result_rgb = result.convert('RGB')
                result = result_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG).convert('L')
            else:
                result = result.convert('1', dither=Image.Dither.FLOYDSTEINBERG).convert('L')
        else: # none
            lut = []
            if not is_xtg:
                for i in range(256):
                    if i < 42: val = 0
                    elif i < 127: val = 85
                    elif i < 212: val = 170
                    else: val = 255
                    lut.append(val)
            else:
                for i in range(256):
                    val = 0 if i < 128 else 255
                    lut.append(val)
            result = result.point(lut)

        if is_xtg:
            # XTG Encoding (Horizontal Scan Order)
            # Ensure 1-bit mode efficiently for standard tobytes()
            xtg_img = result.point(lambda p: 255 if p >= 128 else 0).convert("1")
            data = xtg_img.tobytes()
            magic = b"XTG\x00"
        else:
            # XTH Encoding (Vertical Scan Order)
            w, h = TARGET_WIDTH, TARGET_HEIGHT
            col_bytes = (h + 7) // 8
            plane_size = col_bytes * w
            plane0, plane1 = bytearray(plane_size), bytearray(plane_size)
            pixels = result.load()
            for x in range(w - 1, -1, -1):
                col_idx = w - 1 - x
                for y in range(h):
                    p = pixels[x, y]
                    if p >= 212: val = 0 # White
                    elif p >= 127: val = 1 # Light Gray
                    elif p >= 42: val = 2 # Dark Gray
                    else: val = 3 # Black
                    byte_idx = col_idx * col_bytes + (y // 8)
                    bit_idx = 7 - (y % 8)
                    if val & 1: plane0[byte_idx] |= (1 << bit_idx)
                    if val & 2: plane1[byte_idx] |= (1 << bit_idx)
            data = plane0 + plane1
            magic = b"XTH\x00"

        header = struct.pack("<4sHHBBI8s", magic, TARGET_WIDTH, TARGET_HEIGHT, 0, 0, len(data), hashlib.md5(data).digest()[:8])
        with open(output_path, "wb") as f:
            f.write(header)
            f.write(data)
        print(f"  ✓ {output_path.name} ({ (len(header)+len(data)) // 1024}KB)")
        return True
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False

def main():
    print("=" * 60)
    print("Image to XTH/XTG Converter for XTEink X4")
    print("=" * 60)
    
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print("\nUsage:")
        print("  image2xth image.jpg                      # Default (2-bit XTH, Atkinson, Cover)")
        print("  image2xth image.jpg --xtg                # Output 1-bit XTG file")
        print("  image2xth image.jpg --mode letterbox     # Scale to fit")
        print("  image2xth image.jpg --pad black          # Black padding")
        print("  image2xth image.jpg --dither floyd       # Floyd-Steinberg")
        print("  image2xth image.jpg --downscale box      # Box downscaling")
        print("  image2xth image.jpg --gamma 0.7          # Brighten")
        print("  image2xth folder/                        # Convert all images in folder")
        print("\nDownscaling Algorithms:")
        print("  bicubic (default), bilinear, box, lanczos, nearest")
        return 0
    
    global DOWNSCALE_FILTER
    dither_algo = DITHER_ALGO
    gamma = 1.0
    invert = "--invert" in args
    is_xtg = "--xtg" in args
    mode = 'cover'
    pad_color = 255 # White
    
    if '--dither' in args:
        idx = args.index('--dither')
        if idx + 1 < len(args): dither_algo = args[idx+1].lower()
    if '--downscale' in args:
        idx = args.index('--downscale')
        if idx + 1 < len(args):
            val = args[idx+1].lower()
            if val in DOWNSCALE_MAP:
                DOWNSCALE_FILTER = DOWNSCALE_MAP[val]
    if '--gamma' in args:
        idx = args.index('--gamma')
        if idx + 1 < len(args): gamma = float(args[idx+1])
    if '--mode' in args:
        idx = args.index('--mode')
        if idx + 1 < len(args): mode = args[idx+1].lower()
    if '--pad' in args:
        idx = args.index('--pad')
        if idx + 1 < len(args):
            p = args[idx+1].lower()
            pad_color = 0 if p == 'black' else 255
    
    # Get positional path (ignore args starting with -- and their values)
    skip = False
    input_path = None
    for i, arg in enumerate(sys.argv[1:]):
        if skip:
            skip = False
            continue
        if arg.startswith('--'):
            if arg in ['--dither', '--downscale', '--gamma', '--mode', '--pad']:
                skip = True
            continue
        input_path = Path(arg)
        break
    
    if not input_path or not input_path.exists():
        print("Error: No valid input file or folder specified")
        return 1
    
    ext_out = '.xtg' if is_xtg else '.xth'
    
    if input_path.is_file():
        convert_image(input_path, input_path.with_suffix(ext_out), dither_algo, gamma, invert, mode, pad_color, is_xtg)
    else:
        for ext in SUPPORTED_FORMATS:
            for f in sorted(input_path.glob(f"*{ext}")):
                convert_image(f, f.with_suffix(ext_out), dither_algo, gamma, invert, mode, pad_color, is_xtg)

if __name__ == "__main__":
    main()