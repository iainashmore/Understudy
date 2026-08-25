"""The icon: a stand-in.

Two figures — one in front, its double waiting behind. That is what an
understudy is, and it is what the application is named for.

No spotlight, no beam. Both were tried and both fought the shapes: a gradient
at 16px is a grey smear, and a smear reads as a rendering fault rather than as
light. What carries the idea at every size is the pair itself, and the tone
between them.

Verified here rather than in CI: `main()` reads its own .ico back and asserts
what is in it. electron-builder needs a 256px entry, and Pillow will happily
write an .ico whose largest frame is 16px if you build it from the wrong image
-- which it did, and which cost four red builds to notice.
"""

from PIL import Image, ImageDraw

SS = 4                                  # supersample, then resize down

PLATE = (24, 27, 32)                    # near the application's own background
FRONT = (110, 184, 240)                 # the principal
BEHIND = (70, 90, 110)                  # the understudy, a step behind


def figure(draw, cx, top, scale, colour):
    """Head and shoulders. Nothing clever -- a circle and a rounded arch."""
    head_r = 0.150 * scale
    head_cy = top + head_r
    draw.ellipse((cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
                 fill=colour)

    body_w = 0.480 * scale
    body_top = head_cy + head_r * 1.34
    body_h = 0.360 * scale
    draw.rounded_rectangle(
        (cx - body_w / 2, body_top, cx + body_w / 2, body_top + body_h),
        radius=body_w * 0.42, fill=colour,
    )
    # square off the bottom of the arch so it stands on a baseline
    draw.rectangle((cx - body_w / 2, body_top + body_h * 0.55,
                    cx + body_w / 2, body_top + body_h), fill=colour)


def compose(size):
    W = size * SS
    image = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, W - 1, W - 1), radius=int(W * 0.22), fill=PLATE)

    # The understudy: behind, a little smaller, stepped to the left.
    figure(draw, cx=W * 0.370, top=W * 0.290, scale=W * 0.84, colour=BEHIND)

    # A gap punched around the front figure, cut from the background, so the
    # two silhouettes never merge. This is the difference between "two people"
    # and "a blob" at 16px, and the only reason this survives down there.
    gap = W * 0.044
    cut = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    figure(ImageDraw.Draw(cut), cx=W * 0.605, top=W * 0.228 - gap,
           scale=W * 0.95 + gap * 2, colour=(0, 0, 0))
    image.paste(Image.new("RGBA", (W, W), PLATE + (255,)), (0, 0),
                cut.getchannel("A"))

    draw = ImageDraw.Draw(image)
    figure(draw, cx=W * 0.605, top=W * 0.228, scale=W * 0.95, colour=FRONT)
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    compose(1024).save("desktop/build/icon.png")

    sizes = (16, 24, 32, 48, 64, 128, 256)
    frames = [compose(s) for s in sizes]
    # Built from the LARGEST frame. Pillow takes the base image's size as the
    # icon's size; starting from the 16px one produces a file whose biggest
    # entry is 16px, which electron-builder rejects and nothing else notices.
    frames[-1].save("desktop/build/icon.ico", format="ICO",
                    sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])

    written = sorted(Image.open("desktop/build/icon.ico").info.get("sizes", []))
    assert (256, 256) in written, f"no 256px entry; got {written}"
    assert (16, 16) in written, f"no 16px entry; got {written}"
    print(f"icon.png 1024px | icon.ico {written}")

    for background, name in (((238, 238, 240), "light"), ((32, 34, 38), "dark")):
        sheet = Image.new("RGB", (860, 300), background)
        x = 40
        for frame in frames:
            sheet.paste(frame.convert("RGB"), (x, 150 - frame.width // 2), frame)
            x += frame.width + 26
        sheet.save("/tmp/claude-0/-home-user-harness/"
                   f"15c7ee68-d8b4-55ee-a561-e28f9ec3ed31/scratchpad/icon-{name}.png")


if __name__ == "__main__":
    main()
