"""
Generates a welcome/leave banner image (PNG) with the member's avatar,
name and member count, similar to popular welcomer bots.

Everything is drawn with Pillow so no external image-generation API
is required. Falls back gracefully if a custom background URL fails.
"""
import io
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

CARD_SIZE = (1024, 400)
AVATAR_SIZE = 200

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT_BOLD = f"{FONT_DIR}/DejaVuSans-Bold.ttf"
FONT_REGULAR = f"{FONT_DIR}/DejaVuSans.ttf"


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


async def _fetch_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        return None
    return None


def _make_gradient_background(size, color_top=(35, 39, 42), color_bottom=(88, 101, 242)):
    w, h = size
    base = Image.new("RGB", size, color_top)
    top = Image.new("RGB", size, color_top)
    bottom = Image.new("RGB", size, color_bottom)
    mask = Image.new("L", size)
    mask_data = []
    for y in range(h):
        mask_data.extend([int(255 * (y / h))] * w)
    mask.putdata(mask_data)
    base = Image.composite(bottom, top, mask)
    return base


def _circle_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar = ImageOps.fit(avatar, (size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size))
    result.paste(avatar, (0, 0), mask)
    return result


async def generate_card(
    username: str,
    subtitle: str,
    avatar_url: str,
    background_url: str | None = None,
    accent_rgb: tuple[int, int, int] = (88, 101, 242),
) -> io.BytesIO:
    """Builds the welcome/leave banner and returns it as an in-memory PNG buffer."""
    bg_bytes = await _fetch_bytes(background_url) if background_url else None
    if bg_bytes:
        try:
            bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
            bg = ImageOps.fit(bg, CARD_SIZE, Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(2))
            overlay = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 120))
            bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
        except Exception:
            bg = _make_gradient_background(CARD_SIZE, color_bottom=accent_rgb)
    else:
        bg = _make_gradient_background(CARD_SIZE, color_bottom=accent_rgb)

    card = bg.convert("RGBA")
    draw = ImageDraw.Draw(card)

    # subtle rounded panel behind text for readability
    panel = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(panel)
    pdraw.rounded_rectangle(
        [40, 40, CARD_SIZE[0] - 40, CARD_SIZE[1] - 40],
        radius=28,
        fill=(17, 18, 20, 140),
    )
    card = Image.alpha_composite(card, panel)
    draw = ImageDraw.Draw(card)

    # avatar
    avatar_bytes = await _fetch_bytes(avatar_url)
    avatar_pos = (70, (CARD_SIZE[1] - AVATAR_SIZE) // 2)
    if avatar_bytes:
        avatar_img = _circle_avatar(avatar_bytes, AVATAR_SIZE)
        ring = Image.new("RGBA", (AVATAR_SIZE + 16, AVATAR_SIZE + 16), (0, 0, 0, 0))
        rdraw = ImageDraw.Draw(ring)
        rdraw.ellipse((0, 0, AVATAR_SIZE + 16, AVATAR_SIZE + 16), fill=accent_rgb + (255,))
        card.paste(ring, (avatar_pos[0] - 8, avatar_pos[1] - 8), ring)
        card.paste(avatar_img, avatar_pos, avatar_img)

    # text
    text_x = avatar_pos[0] + AVATAR_SIZE + 50
    name_font = _load_font(FONT_BOLD, 54)
    sub_font = _load_font(FONT_REGULAR, 30)

    max_name_len = 18
    display_name = username if len(username) <= max_name_len else username[: max_name_len - 1] + "…"

    draw.text((text_x, 145), display_name, font=name_font, fill=(255, 255, 255, 255))
    draw.text((text_x, 215), subtitle, font=sub_font, fill=(210, 210, 215, 255))

    buffer = io.BytesIO()
    card.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
