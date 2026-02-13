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
    atkinson (default), floyd, none
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
DITHER_ALGO = "atkinson" # "atkinson", "floyd", "none"

# Configuration
TARGET_WIDTH = 480
TARGET_HEIGHT = 800
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff', '.tif'}

@njit
def _atkinson_loop(data, w, h, stride):
    for y in range(h):
        row_start = y * stride
        for x in range(1, w + 1):
            idx = row_start + x
            old_val = data[idx]
            if old_val < 42: new_val = 0
            elif old_val < 127: new_val = 85
            elif old_val < 212: new_val = 170
            else: new_val = 255
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

def dither_atkinson(img):
    w, h = img.size
    stride = w + 3
    buff = np.zeros((h + 3, stride), dtype=np.int16)
    img_arr = np.array(img, dtype=np.int16)
    buff[0:h, 1:w+1] = img_arr
    data = buff.flatten()
    _atkinson_loop(data, w, h, stride)
    res_arr = data.reshape((h + 3, stride))
    final_arr = np.clip(res_arr[0:h, 1:w+1], 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr, 'L')

def convert_to_xth(input_path, output_path, dither_algo='atkinson', gamma=1.0, invert=False, mode='cover', pad_color=255):
    try:
        img = Image.open(input_path)
        if img.mode != 'L':
            img = img.convert('L')
        
        img_width, img_height = img.size
        
        if mode == 'fill':
            img_resized = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.BICUBIC)
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
            img_resized = img.resize((new_width, new_height), Image.Resampling.BICUBIC)
            left = (new_width - TARGET_WIDTH) // 2
            top = (new_height - TARGET_HEIGHT) // 2
            result = img_resized.crop((left, top, left + TARGET_WIDTH, top + TARGET_HEIGHT))
        else: # letterbox
            scale = min(TARGET_WIDTH / img_width, TARGET_HEIGHT / img_height)
            new_width, new_height = int(img_width * scale), int(img_height * scale)
            img_resized = img.resize((new_width, new_height), Image.Resampling.BICUBIC)
            result = Image.new('L', (TARGET_WIDTH, TARGET_HEIGHT), color=pad_color)
            x, y = (TARGET_WIDTH - new_width) // 2, (TARGET_HEIGHT - new_height) // 2
            result.paste(img_resized, (x, y))
        
        if invert:
            result = ImageOps.invert(result)
        
        if gamma != 1.0:
            lut = [int(((i / 255.0) ** gamma) * 255.0) for i in range(256)]
            result = result.point(lut)

        if dither_algo == 'atkinson':
            result = dither_atkinson(result)
        elif dither_algo == 'floyd':
            pal_img = Image.new("P", (1, 1))
            pal_img.putpalette([0,0,0, 85,85,85, 170,170,170, 255,255,255] + [0,0,0]*252)
            result_rgb = result.convert('RGB')
            result = result_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG).convert('L')
        else: # none
            lut = []
            for i in range(256):
                if i < 42: val = 0
                elif i < 127: val = 85
                elif i < 212: val = 170
                else: val = 255
                lut.append(val)
            result = result.point(lut)

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
        header = struct.pack("<4sHHBBI8s", b"XTH\x00", w, h, 0, 0, len(data), hashlib.md5(data).digest()[:8])
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
    print("Image to 2-bit XTH Converter for XTEink X4")
    print("=" * 60)
    
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print("\nUsage:")
        print("  image2xth image.jpg                      # Default (Atkinson, Cover)")
        print("  image2xth image.jpg --mode letterbox     # Scale to fit")
        print("  image2xth image.jpg --pad black          # Black padding")
        print("  image2xth image.jpg --dither floyd       # Floyd-Steinberg")
        print("  image2xth image.jpg --gamma 0.7          # Brighten")
        print("  image2xth folder/                        # Convert all images")
        return 0
    
    dither_algo = DITHER_ALGO
    gamma = 1.0
    invert = "--invert" in args
    mode = 'cover'
    pad_color = 255 # White
    
    if '--dither' in args:
        idx = args.index('--dither')
        if idx + 1 < len(args): dither_algo = args[idx+1].lower()
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
            if arg in ['--dither', '--gamma', '--mode', '--pad']:
                skip = True
            continue
        input_path = Path(arg)
        break
    
    if not input_path or not input_path.exists():
        print("Error: No valid input file or folder specified")
        return 1
    
    if input_path.is_file():
        convert_to_xth(input_path, input_path.with_suffix('.xth'), dither_algo, gamma, invert, mode, pad_color)
    else:
        for ext in SUPPORTED_FORMATS:
            for f in sorted(input_path.glob(f"*{ext}")):
                convert_to_xth(f, f.with_suffix('.xth'), dither_algo, gamma, invert, mode, pad_color)

if __name__ == "__main__":
    main()