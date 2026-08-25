"""The icon: layers — the same thing, again and again.

Three identical plates, stacked and offset, the top one lit. That is what the
application does: one path through an interface, replayed as many times as it
is asked to, each run a copy of the last with only the prompt changed.

Abstract on purpose. Figures and spotlights kept sliding towards the generic
"contacts" icon; flat offset plates cannot, and they hold their shape all the
way down to 16px, which is the size that decides whether an icon works.

Verified here rather than in CI: `main()` reads its own .ico back and asserts
what is in it. electron-builder needs a 256px entry, and Pillow will happily
write an .ico whose largest frame is 16px if you build it from the wrong image
-- which it did, and which cost four red builds to notice.
"""

from PIL import Image, ImageDraw

SS = 4                                  # supersample, then resize down

PLATE = (24, 27, 32)                    # near the application's own background
TOP = (110, 184, 240)                   # the run in front
MID = (58, 96, 134)
LOW = (42, 62, 84)


def compose(size):
    W = size * SS
    image = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, W - 1, W - 1), radius=int(W * 0.22), fill=PLATE)

    width, height = W * 0.560, W * 0.300
    radius = int(W * 0.055)
    left = W * 0.500 - width / 2
    step = W * 0.128
    # A dark rim between the plates, cut from the background, so three plates
    # never merge into one tall block at 16px.
    rim = W * 0.038

    for index, (colour, top) in enumerate((
        (LOW, W * 0.185), (MID, W * 0.185 + step), (TOP, W * 0.185 + step * 2),
    )):
        if index:
            draw.rounded_rectangle(
                (left - rim, top - rim, left + width + rim, top + height + rim),
                radius=radius + int(rim), fill=PLATE)
        draw.rounded_rectangle((left, top, left + width, top + height),
                               radius=radius, fill=colour)

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
