# XTEink Manga Tools

Tools for converting CBZ, PDF, and images to XTC/XTCH format for the XTEink X4 e-reader.

## Requirements

- Python 3.7+
- [Pillow](https://pillow.readthedocs.io/)
- [NumPy](https://numpy.org/)
- [Numba](https://numba.pydata.org/) 
- [PyMuPDF](https://pymupdf.readthedocs.io/) (For PDF extraction in `cbz2xtc.py`)
- `poppler-utils` (For PDF extraction in `cbz2xtcpoppler.py`)

## Tools

### cbz2xtc.py / cbz2xtcpoppler.py
Converts CBZ and PDF files to XTC/XTCH.
- **1-bit (XTC)** and **2-bit (XTCH)** output.
- **Numpy acceleration**: Encodes images using NumPy to replace Python loops.
- **Multi-threaded**: Processes multiple pages in parallel.
- **Page Splitting**: Automatically splits landscape spreads into portrait segments.
- **Overviews**: Generates full-page overviews (sideways or upright).
- **Navigation**: Generates Table of Contents (TOC) for jump navigation.

### image2xth.py
Converts individual images to 2-bit XTCH.
- Supports `cover`, `letterbox`, `fill`, and `crop` scaling.
- Customizable dithering and padding.

```bash
# Basic conversion (Atkinson dither, Cover scaling)
./image2xth.py wallpaper.jpg

# Scale to fit with black padding
./image2xth.py wallpaper.jpg --mode letterbox --pad black

# Use Floyd-Steinberg dithering and brighten
./image2xth.py wallpaper.jpg --dither floyd --gamma 0.7
```

### image2bw.py
Converts individual images to 1-bit BMP.

```bash
# Basic conversion (Floyd-Steinberg dither)
./image2bw.py background.png

# Pure threshold (no dithering)
./image2bw.py background.png --dither none
```

## Usage

### cbz2xtc.py

```bash
# Basic conversion (1-bit XTC, Left-to-Right splitting)
./cbz2xtc.py /path/to/folder

# 2-bit Grayscale (XTCH) with Atkinson dithering
./cbz2xtc.py --2bit --dither atkinson

# Right-to-Left landscape spread splitting
./cbz2xtc.py --2bit --landscape-rtl

# Add full-page overviews (upright portrait)
./cbz2xtc.py --2bit --include-overviews

# Sideways overviews (rotated -90 degrees)
./cbz2xtc.py --2bit --sideways-overviews

# Clean up temporary files after conversion
./cbz2xtc.py --clean
```

### Common Flags

| Flag | Description |
| :--- | :--- |
| `--2bit` | Output 2-bit grayscale (.xtch) instead of 1-bit (.xtc) |
| `--dither <algo>` | select: `floyd`, `atkinson`, `ordered`, `rasterize`, `none` |
| `--landscape-rtl` | Process landscape spreads from Right to Left |
| `--gamma <float>` | Adjust brightness (e.g., `0.7` to brighten) |
| `--invert` | Invert colors |
| `--margin <float>` | Crop margins by percentage |
| `--clean` | Delete intermediate PNG files after processing |


## Credits

- Original base tool by [tazua/cbz2xtc](https://github.com/tazua/cbz2xtc).
- Format reference: [bigbag/epub-to-xtc-converter](https://github.com/bigbag/epub-to-xtc-converter).
