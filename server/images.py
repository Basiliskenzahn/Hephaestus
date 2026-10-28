"""Normalise uploads: any of JPEG/PNG/HEIC, EXIF orientation applied then stripped, longest side <= MAX_IMAGE_SIDE."""
import io

from PIL import Image, ImageOps

from .config import MAX_IMAGE_SIDE, MAX_UPLOAD_BYTES, MIN_IMAGE_SIDE

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # HEIC support is optional locally
    pillow_heif = None


class UploadError(ValueError):
    pass


def normalise(data):
    """Returns JPEG bytes without metadata. Raises UploadError with a message for the user."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"the image is {len(data) / 1e6:.1f} MB; the limit is {MAX_UPLOAD_BYTES // 1_000_000} MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001 - any decoder error means "not an image we can read"
        raise UploadError("this file is not a JPEG, PNG or HEIC image") from None
    if img.format not in ("JPEG", "PNG", "HEIF", "HEIC", "MPO"):
        raise UploadError(f"{img.format} images are not supported; use JPEG, PNG or HEIC")
    if min(img.size) < MIN_IMAGE_SIDE:
        raise UploadError(f"the image is only {img.size[0]} x {img.size[1]} px: too small to read dimensions. Send "
                          f"at least {MIN_IMAGE_SIDE} px on the short side")
    img = ImageOps.exif_transpose(img)            # keep the orientation the phone recorded, then drop the EXIF
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)   # no exif= argument: metadata is not written
    return out.getvalue()
