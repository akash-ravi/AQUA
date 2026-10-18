import base64
import binascii
import io

from PIL import Image as PilImage
from PIL import ImageDraw, ImageFont

PilImage.MAX_IMAGE_PIXELS = 20_000_000
MAX_SIDE = 1600
BOX = (233, 30, 99)
LABEL_TEXT = (255, 255, 255)


def marked_picture(image, view) -> bytes | str:
    """The phone's screenshot with every numbered item boxed and labelled, as JPEG bytes (or a plain problem)."""
    try:
        raw = base64.b64decode(image.data, validate=True)
    except (binascii.Error, ValueError):
        return "the picture is not valid base64"
    if not (raw.startswith(b"\xff\xd8") or raw.startswith(b"\x89PNG")):
        return "the picture is not a JPEG or PNG"
    try:
        picture = PilImage.open(io.BytesIO(raw))
        picture.load()
    except (OSError, PilImage.DecompressionBombError):
        return "the picture could not be read"
    picture = picture.convert("RGB")

    scale = picture.width / view.screen.width if view.screen.width else 1.0
    draw = ImageDraw.Draw(picture)
    font = _font(max(12, int(picture.width / 40)))
    for line in view.lines:
        left, top, right, bottom = (round(value * scale) for value in line.node.bounds)
        draw.rectangle((left, top, right, bottom), outline=BOX, width=2)
        number = str(line.index)
        text_box = draw.textbbox((0, 0), number, font=font)
        width, height = text_box[2] - text_box[0] + 6, text_box[3] - text_box[1] + 6
        draw.rectangle((left, top, left + width, top + height), fill=BOX)
        draw.text((left + 3, top + 1), number, fill=LABEL_TEXT, font=font)

    longest = max(picture.size)
    if longest > MAX_SIDE:
        ratio = MAX_SIDE / longest
        picture = picture.resize((round(picture.width * ratio), round(picture.height * ratio)))
    out = io.BytesIO()
    picture.save(out, format="JPEG", quality=80)
    return out.getvalue()


def needs_picture(view) -> bool:
    """True when the screen text alone probably isn't enough (unlabelled buttons, canvas or web content)."""
    clickable = [line for line in view.lines if line.node.clickable]
    if not clickable:
        return False
    unlabelled = [line for line in clickable if line.label.startswith("(no label")]
    texts = [line for line in view.lines if line.label and not line.label.startswith("(no label")]
    return len(unlabelled) / len(clickable) >= 0.3 or not texts


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()
