"""Spectra 6 (6-color) e-paper support.

The Waveshare Photo Painter and Seeed reTerminal E1002 use a 7.3" Spectra 6
color e-paper panel (EP73_SPECTRA_800x480). The panel is a 4-bpp display, so
its framebuffer is (800 >> 1) * 480 = 192,000 bytes = 192 KB.

The TRMNL firmware decodes the PNG it downloads and maps every pixel to one of
six Spectra 6 colors using the palette below (see iSpectraRGB[] in the
firmware's src/display.cpp). Because the firmware does its own RGB -> Spectra 6
color matching (GetSpectraPixel), it accepts either a full-color PNG or a
pre-dithered 6-color PNG. We serve a dithered 6-color palette PNG: it is small
enough to fit the firmware's MAX_IMAGE_SIZE and it bakes the dithering in so the
on-screen result matches what we rendered.
"""

import io

from PIL import Image

# The six Spectra 6 colors, in the exact order the firmware expects
# (iSpectraRGB[] in src/display.cpp). Index == Spectra 6 palette index.
#   0 = black, 1 = white, 2 = yellow, 3 = red, 4 = blue, 5 = green
SPECTRA6_PALETTE = (
    (0, 0, 0),       # black  = 0
    (192, 192, 192), # white  = 1
    (192, 192, 0),   # yellow = 2
    (192, 0, 0),     # red    = 3
    (0, 0, 192),     # blue   = 4
    (0, 192, 0),     # green  = 5
)

# Model header values (sent by the firmware in the "Model" request header) that
# identify a color Spectra 6 panel. Anything else is treated as a monochrome
# device and served the usual grayscale image.
SPECTRA6_MODELS = frozenset({
    'waveshare_photo_painter',
    'reterminal_e1002',
})


def is_spectra6_model(model):
    """Return True if the reported Model header identifies a Spectra 6 panel."""
    if not model:
        return False
    return model.strip().lower() in SPECTRA6_MODELS


def _build_palette_image():
    """Build a 1x1 'P' image whose palette is the Spectra 6 palette.

    Pillow's Image.quantize(palette=...) uses this image's palette as the
    target color set. The palette must be padded to 256 entries (768 bytes).
    """
    flat = []
    for r, g, b in SPECTRA6_PALETTE:
        flat.extend((r, g, b))
    # Pad the remaining 250 slots with black so the palette is a full 256 entries.
    flat.extend((0, 0, 0) * (256 - len(SPECTRA6_PALETTE)))
    pal_image = Image.new('P', (1, 1))
    pal_image.putpalette(flat)
    return pal_image


def convert_to_spectra6(buf):
    """Convert a PNG buffer to a dithered 6-color Spectra 6 palette PNG.

    The input buffer may hold any PNG (grayscale, RGB, RGBA, palette). The
    output is a mode-'P' PNG whose palette is exactly the Spectra 6 palette,
    dithered with Floyd-Steinberg so mid-tones render as the panel's six
    colors. Returns a new BytesIO positioned at the start.

    If the image cannot be opened or converted, the original buffer is
    returned (repositioned to the start) so the caller still serves a valid
    image rather than failing the request.
    """
    try:
        buf.seek(0)
        img = Image.open(buf)
        # Work in RGB so quantization sees true colors regardless of source mode.
        if img.mode != 'RGB':
            img = img.convert('RGB')
        quantized = img.quantize(
            palette=_build_palette_image(),
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        new_buf = io.BytesIO()
        quantized.save(new_buf, format='PNG')
        new_buf.seek(0)
        return new_buf
    except Exception:
        # Never let the color conversion break image serving: fall back to the
        # original (grayscale) buffer, which the firmware renders fine.
        buf.seek(0)
        return buf
