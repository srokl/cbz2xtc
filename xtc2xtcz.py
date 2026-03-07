#!/usr/bin/env python3
"""
xtc2xtcz - Compress XTC/XTCH files to XTCZ format for XTEink X4

Usage:
    xtc2xtcz <file.xtc>        # Compress a single file
    xtc2xtcz <folder>          # Compress all .xtc/.xtch files in a folder
"""

import sys
import struct
from pathlib import Path

try:
    import lz4.block
except ImportError:
    print("Error: lz4 not installed. Please run 'pip install lz4'")
    sys.exit(1)


def compress_to_xtcz(input_path, output_path):
    print(f"Compressing {input_path.name} -> {output_path.name}...", end=" ", flush=True)
    block_size = 4096
    try:
        uncompressed_size = input_path.stat().st_size
        with open(input_path, "rb") as f_in, open(output_path, "wb") as f_out:
            # XTZ4 Header: Magic (4 bytes), Uncompressed Size (uint32), Block Size (uint32)
            f_out.write(struct.pack("<4sII", b"XTZ4", uncompressed_size, block_size))
            
            while True:
                chunk = f_in.read(block_size)
                if not chunk:
                    break
                
                compressed = lz4.block.compress(chunk, store_size=False)
                
                # If compression doesn't save space, store uncompressed
                if len(compressed) >= len(chunk):
                    descriptor = len(chunk) | 0x80000000
                    f_out.write(struct.pack("<I", descriptor))
                    f_out.write(chunk)
                else:
                    descriptor = len(compressed)
                    f_out.write(struct.pack("<I", descriptor))
                    f_out.write(compressed)
                    
        orig_mb = uncompressed_size / 1024 / 1024
        new_mb = output_path.stat().st_size / 1024 / 1024
        ratio = (1 - (new_mb / orig_mb)) * 100 if orig_mb > 0 else 0
        
        print(f"✓ ({orig_mb:.1f}MB -> {new_mb:.1f}MB, -{ratio:.1f}%)")
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("============================================================")
        print("XTC to XTCZ Compressor for XTEink X4")
        print("============================================================")
        print("\nCompresses existing .xtc and .xtch files into the .xtcz format using LZ4.")
        print("\nUsage:")
        print("  xtc2xtcz <file.xtc>        # Compress a single file")
        print("  xtc2xtcz <folder>          # Compress all .xtc/.xtch files in a folder")
        return 0

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: Path '{input_path}' does not exist.")
        return 1

    files_to_process = []
    if input_path.is_file():
        if input_path.suffix.lower() in [".xtc", ".xtch"]:
            files_to_process.append(input_path)
        else:
            print(f"Error: '{input_path}' is not an .xtc or .xtch file.")
            return 1
    elif input_path.is_dir():
        files_to_process.extend(sorted(input_path.glob("*.xtc")))
        files_to_process.extend(sorted(input_path.glob("*.xtch")))
        files_to_process.extend(sorted(input_path.glob("*.XTC")))
        files_to_process.extend(sorted(input_path.glob("*.XTCH")))

    if not files_to_process:
        print(f"No .xtc or .xtch files found in '{input_path}'.")
        return 1

    print(f"Found {len(files_to_process)} file(s) to compress.\n")
    
    success_count = 0
    for file_path in files_to_process:
        output_path = file_path.with_suffix(".xtcz")
        if compress_to_xtcz(file_path, output_path):
            success_count += 1

    print(f"\nCompleted! Successfully compressed {success_count}/{len(files_to_process)} files.")
    return 0 if success_count == len(files_to_process) else 1


if __name__ == "__main__":
    sys.exit(main())
