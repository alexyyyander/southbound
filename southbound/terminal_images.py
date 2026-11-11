"""Bounded JPEG headers for phone screenshot transport (not image decoding)."""
MAX_IMAGE_BYTES = 512 * 1024
MAX_IMAGE_EDGE = 1280


def jpeg_dimensions(data: bytes):
    if not 12 <= len(data) <= MAX_IMAGE_BYTES or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('expected a JPEG of at most 512 KiB')
    index = 2
    while index + 4 <= len(data):
        if data[index] != 255:
            break
        while index < len(data) and data[index] == 255:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in (0xD9, 0xDA):
            break
        length = int.from_bytes(data[index:index+2], 'big')
        if length < 2 or index + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
            height = int.from_bytes(data[index+3:index+5], 'big')
            width = int.from_bytes(data[index+5:index+7], 'big')
            if not (1 <= width <= MAX_IMAGE_EDGE and 1 <= height <= MAX_IMAGE_EDGE):
                raise ValueError('screenshot dimensions exceed 1280 pixels')
            return width, height
        index += length
    raise ValueError('invalid JPEG frame header')
