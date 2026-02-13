# XTEink Manga Tools

A collection of command-line tools to convert manga (CBZ), PDF files, and images to XTC/XTCH format for the **XTEink X4** e-reader. Optimized for fast loading, high-quality grayscale, and smart navigation.

## 🚀 Features

- **cbz2xtc** - Powerful standalone converter for CBZ and PDF manga files.
  - **2-bit Grayscale Mode (.xtch)** - 4 levels of gray for significantly better image quality.
  - **Full PDF Support** - Direct conversion of PDF files (requires `pymupdf`).
  - **Strict Standard Compliance** - Generates files strictly following the XTC v1.0 specification.
  - **Smart Chapter Navigation** -
    - **Folder-based Chapters**: Automatically names chapters based on folders inside CBZ (e.g., `Page 1 - The Beginning`).
    - **Automatic Page Markers**: Every page gets a "Page n" marker for easy jumping.
  - **Dithering Options** -
    - **Atkinson**: sharp, clean shading.
    - **Floyd-Steinberg**: Smooth gradients.
    - **Ordered & Rasterize**: Classic patterns for distinct e-ink looks.
  - **Image Controls** -
    - **Gamma Correction**: Adjust midtone brightness (`--gamma 0.7`).
    - **Color Inversion**: Flip Black/White polarity (`--invert`).
  - **Multithreaded Speed** - Up to 4x processing using parallel workers.

- **image2xth** - High-quality 2-bit XTH converter for individual images.
  - Uses **Atkinson** dithering by default (sharpest results).
  - Supports Floyd-Steinberg and pure quantization modes.
  - Advanced scaling modes: `cover` (default), `letterbox`, `fill`, and `crop`.
  - Customizable padding: `--pad white` (default) or `--pad black`.
  - Ideal for high-quality e-ink backgrounds and wallpapers.
  
- **image2bw** - Utility to convert individual images to 1-bit BMP for backgrounds.

## 📋 Requirements

- Python 3.7+
- [Pillow](https://pillow.readthedocs.io/)
- [NumPy](https://numpy.org/)
- [Numba](https://numba.pydata.org/) 
- [PyMuPDF](https://pymupdf.readthedocs.io/) (Optional for **PDF** extraction)

## 🔧 Installation

```bash
# Install Python dependencies
pip install pillow numpy numba pymupdf
```

## 📖 Usage

### cbz2xtc - Convert CBZ/PDF to XTC/XTCH

 
```bash
# Basic usage (1-bit XTC, split pages)
./cbz2xtc.py

# High Quality 2-bit Grayscale (.xtch) - RECOMMENDED
./cbz2xtc.py --2bit

# High Quality with Atkinson Dithering (Sharpest results)
./cbz2xtc.py --2bit --dither atkinson

# Add Full-Page Overviews (Rotated sideways)
./cbz2xtc.py --2bit --sideways-overviews

# Brighten images
./cbz2xtc.py --2bit --gamma 0.6

# Process specific file or folder
./cbz2xtc.py /path/to/manga/
```

**Output:** 
- 1-bit: `./xtc_output/*.xtc`
- 2-bit: `./xtc_output/*.xtch`

## 🎯 Recommended Settings

| Content Type | Recommended Command |
| :--- | :--- |
| **Standard Manga** | `cbz2xtc --2bit --dither atkinson --clean` |
| **Dark Lineart** | `cbz2xtc --2bit --gamma 0.7 --clean` |
| **Western Comics** | `cbz2xtc --2bit --dither floyd --clean` |
| **High Sharpness** | `cbz2xtc --2bit --dither none --clean` |

## 📐 Specifications

- **Device:** XTEink X4 (ESP32-based)
- **Target Size:** 480×800 pixels
- **Format Support:** 1-bit XTC, 2-bit XTCH (Vertical scan order)

## 🙏 Credits

- Original base tool by [tazua/cbz2xtc](https://github.com/tazua/cbz2xtc).
- Technical specification and as reference from [bigbag/epub-to-xtc-converter](https://github.com/bigbag/epub-to-xtc-converter).

---

**Enjoy reading manga on your XTEink X4!** 📚✨
